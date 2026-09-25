from __future__ import annotations

import asyncio
import html
import logging
import os
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote

from app.database import session_factory
from app.models import EventoOperacional
from app.settings import Settings, get_settings

logger = logging.getLogger("ze_registra.emailing")

# Achado do indicador "e-mails rejeitados", pendente em
# docs/slo-e-criterios-incidente.md (Fase 6). Sem webhook de provedor, a
# única confirmação de rejeição disponível é o próprio SMTP recusando a
# mensagem na hora do envio (SMTPResponseException e subclasses -- código
# 5xx -- ou SMTPRecipientsRefused, todos os destinatários recusados). Erro
# de timeout/conexão (SMTPServerDisconnected, TimeoutError, OSError) é
# falha transitória, não rejeição, e não é contado aqui.
_ERROS_REJEICAO_SMTP = (smtplib.SMTPResponseException, smtplib.SMTPRecipientsRefused)
_PROVEDORES_ESGOTADOS_ATE: dict[str, float] = {}


@dataclass(frozen=True)
class ProvedorSMTP:
    identificador: str
    nome: str
    email_from_address: str
    email_from_name: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_starttls: bool
    smtp_ssl: bool
    smtp_timeout_seconds: float
    limite_diario: int
    alerta_percentual: int


def _provedor_principal(settings: Settings) -> ProvedorSMTP:
    return ProvedorSMTP(
        identificador="principal",
        nome="Principal",
        email_from_address=settings.email_from_address,
        email_from_name=settings.email_from_name,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_password=settings.smtp_password,
        smtp_starttls=settings.smtp_starttls,
        smtp_ssl=settings.smtp_ssl,
        smtp_timeout_seconds=settings.smtp_timeout_seconds,
        limite_diario=max(1, settings.email_daily_limit),
        alerta_percentual=max(1, min(100, settings.email_daily_warning_percent)),
    )


def _provedor_secundario(settings: Settings) -> ProvedorSMTP | None:
    remetente = settings.smtp_secondary_from_address or settings.smtp_secondary_username
    if not settings.smtp_secondary_enabled or not settings.smtp_secondary_host or not remetente:
        return None
    return ProvedorSMTP(
        identificador="secundario",
        nome=settings.smtp_secondary_name.strip() or "Secundário",
        email_from_address=remetente,
        email_from_name=settings.smtp_secondary_from_name,
        smtp_host=settings.smtp_secondary_host,
        smtp_port=settings.smtp_secondary_port,
        smtp_username=settings.smtp_secondary_username,
        smtp_password=settings.smtp_secondary_password,
        smtp_starttls=settings.smtp_secondary_starttls,
        smtp_ssl=settings.smtp_secondary_ssl,
        smtp_timeout_seconds=settings.smtp_secondary_timeout_seconds,
        limite_diario=max(1, settings.email_secondary_daily_limit),
        alerta_percentual=max(1, min(100, settings.email_secondary_daily_warning_percent)),
    )


def listar_provedores_email(settings: Settings, operacao: str) -> list[ProvedorSMTP]:
    """Resolve o pool sem expor credenciais nem alterar o modo legado."""
    principal = _provedor_principal(settings)
    secundario = _provedor_secundario(settings)
    estrategia = settings.email_provider_strategy.strip().lower()
    if secundario is None or estrategia == "single":
        return [principal]
    if estrategia == "failover":
        return [principal, secundario]
    if estrategia == "category":
        operacoes_secundarias = {
            item.strip() for item in settings.email_secondary_operations.split(",") if item.strip()
        }
        return [secundario] if operacao in operacoes_secundarias else [principal]
    logger.warning("Estratégia de e-mail inválida; usando somente o provedor principal")
    return [principal]


async def _registrar_email_rejeitado(operacao: str, exc: Exception) -> None:
    """Reaproveita EventoOperacional (componente="email"), a mesma tabela que
    já alimenta o painel técnico, em vez de criar uma tabela nova. Abre
    sessão própria porque emailing.py é chamado tanto de dentro de uma
    requisição HTTP quanto do worker em segundo plano, sem sessão
    compartilhada disponível (mesmo padrão de app.observability._registrar)."""
    if not isinstance(exc, _ERROS_REJEICAO_SMTP):
        return
    if getattr(exc, "_zeregistra_rejeicao_registrada", False):
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        async with session_factory() as session:
            session.add(
                EventoOperacional(
                    componente="email",
                    operacao=operacao,
                    sucesso=False,
                    duracao_ms=0,
                    status_http=0,
                    codigo_erro=type(exc).__name__,
                    detalhes={
                        "mensagem": str(exc)[:300],
                        "motivo": "quota_diaria" if erro_cota_diaria_email(exc) else "rejeitado_smtp",
                        "provedor": getattr(exc, "_zeregistra_provedor", "principal"),
                    },
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Falha ao registrar e-mail rejeitado (operacao=%s)", operacao)


def erro_cota_diaria_email(exc: Exception) -> bool:
    """Reconhece a resposta padronizada 5.4.5 sem acoplar a rota ao Gmail."""
    codigo = getattr(exc, "smtp_code", None)
    resposta = getattr(exc, "smtp_error", b"")
    if isinstance(resposta, bytes):
        resposta = resposta.decode("utf-8", errors="replace")
    texto = f"{exc} {resposta}".lower()
    return codigo == 550 and (
        "5.4.5" in texto
        or "daily user sending limit exceeded" in texto
        or "daily smtp relay limit exceeded" in texto
    )


async def _registrar_email_enviado(operacao: str, provedor: ProvedorSMTP) -> None:
    """Telemetria sem destinatário/assunto: permite acompanhar a cota sem PII."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        async with session_factory() as session:
            session.add(
                EventoOperacional(
                    componente="email",
                    operacao=operacao,
                    sucesso=True,
                    duracao_ms=0,
                    status_http=0,
                    codigo_erro=None,
                    detalhes={"provedor": provedor.identificador},
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Falha ao contabilizar e-mail enviado (operacao=%s)", operacao)


async def _enviar_smtp_contabilizado(mensagem: EmailMessage, settings: Settings, operacao: str) -> None:
    provedores = listar_provedores_email(settings, operacao)
    disponiveis = [
        provedor
        for provedor in provedores
        if _PROVEDORES_ESGOTADOS_ATE.get(provedor.identificador, 0) <= time.monotonic()
    ]
    if not disponiveis:
        erro = smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded")
        erro._zeregistra_provedor = provedores[0].identificador
        raise erro
    for indice, provedor in enumerate(disponiveis):
        if "From" in mensagem:
            mensagem.replace_header("From", f"{provedor.email_from_name} <{provedor.email_from_address}>")
        else:
            mensagem["From"] = f"{provedor.email_from_name} <{provedor.email_from_address}>"
        try:
            await asyncio.to_thread(_enviar_smtp, mensagem, provedor)
            _PROVEDORES_ESGOTADOS_ATE.pop(provedor.identificador, None)
            await _registrar_email_enviado(operacao, provedor)
            return
        except Exception as exc:
            exc._zeregistra_provedor = provedor.identificador
            if not erro_cota_diaria_email(exc):
                raise
            # A resposta 550/5.4.5 é definitiva para esta tentativa; portanto,
            # é seguro tentar o próximo remetente sem risco de envio duplicado.
            espera = max(1, settings.email_provider_quota_cooldown_minutes) * 60
            _PROVEDORES_ESGOTADOS_ATE[provedor.identificador] = time.monotonic() + espera
            await _registrar_email_rejeitado(operacao, exc)
            exc._zeregistra_rejeicao_registrada = True
            if indice == len(disponiveis) - 1:
                raise


def _link_recuperacao(settings: Settings, token: str) -> str:
    base = settings.app_public_url.rstrip("/")
    # O fragmento não é enviado ao servidor nem incluído em logs HTTP/referrers.
    return f"{base}/redefinir-senha#token={quote(token, safe='')}"


def _mensagem_recuperacao(destinatario: str, nome: str, token: str, settings: Settings) -> EmailMessage:
    link = _link_recuperacao(settings, token)
    nome_seguro = html.escape(nome or "usuário")
    minutos = settings.password_reset_minutes
    mensagem = EmailMessage()
    mensagem["Subject"] = "Redefinição de senha — Zé Registra"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    mensagem.set_content(
        f"Olá, {nome or 'usuário'}.\n\n"
        "Recebemos uma solicitação para redefinir sua senha no Centro de Operações.\n"
        f"Acesse o link abaixo em até {minutos} minutos:\n\n{link}\n\n"
        "Se você não solicitou a alteração, ignore esta mensagem."
    )
    mensagem.add_alternative(
        f"""
        <!doctype html>
        <html lang="pt-BR"><body style="margin:0;background:#f5f3eb;padding:28px;">
          <main style="max-width:600px;margin:auto;background:#fff;border:1px solid #d8ddd6;
                       border-radius:20px;padding:36px;font-family:Arial,sans-serif;color:#10251d;">
            <p style="margin:0;color:#08704d;font-weight:700;letter-spacing:.08em;">
              ZÉ REGISTRA® · CENTRO DE OPERAÇÕES
            </p>
            <h1 style="font-size:30px;margin:22px 0 12px;">Redefina sua senha</h1>
            <p>Olá, {nome_seguro}.</p>
            <p>Recebemos uma solicitação para redefinir sua senha. Este link é individual,
               pode ser utilizado uma única vez e expira em {minutos} minutos.</p>
            <p style="margin:28px 0;">
              <a href="{html.escape(link, quote=True)}"
                 style="display:inline-block;background:#086044;color:#fff;text-decoration:none;
                        border-radius:10px;padding:14px 22px;font-weight:700;">
                Criar nova senha
              </a>
            </p>
            <p style="color:#607068;font-size:13px;">
              Se você não solicitou a alteração, ignore esta mensagem. Sua senha continuará válida.
            </p>
          </main>
        </body></html>
        """,
        subtype="html",
    )
    return mensagem


def _enviar_smtp(mensagem: EmailMessage, settings: Settings | ProvedorSMTP) -> None:
    if settings.smtp_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=ssl.create_default_context(),
        ) as smtp:
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(mensagem)
        return
    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
    ) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(mensagem)


async def enviar_recuperacao_senha(destinatario: str, nome: str, token: str) -> None:
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = _mensagem_recuperacao(destinatario, nome, token, settings)
    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, settings.smtp_max_attempts) + 1):
        try:
            await _enviar_smtp_contabilizado(mensagem, settings, "recuperacao_senha")
            return
        except Exception as exc:
            ultimo_erro = exc
            if erro_cota_diaria_email(exc):
                break
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
        await _registrar_email_rejeitado("recuperacao_senha", ultimo_erro)
        raise ultimo_erro


def _mensagem_confirmacao_exclusao(destinatario: str, token: str, settings: Settings) -> EmailMessage:
    base = settings.app_public_url.rstrip("/")
    link = f"{base}/privacidade/confirmar-exclusao#token={quote(token, safe='')}"
    minutos = settings.anonimizacao_token_minutos
    horas = max(1, minutos // 60)
    mensagem = EmailMessage()
    mensagem["Subject"] = "Confirme a exclusão dos seus dados — Zé Registra"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    mensagem.set_content(
        "Recebemos um pedido para apagar seus dados de contato do nosso sistema.\n\n"
        f"Se foi você quem solicitou, confirme em até {horas}h acessando o link abaixo:\n\n{link}\n\n"
        "Se você não fez esse pedido, ignore esta mensagem -- nada será alterado."
    )
    mensagem.add_alternative(
        f"""
        <!doctype html>
        <html lang="pt-BR"><body style="margin:0;background:#f5f3eb;padding:28px;">
          <main style="max-width:600px;margin:auto;background:#fff;border:1px solid #d8ddd6;
                       border-radius:20px;padding:36px;font-family:Arial,sans-serif;color:#10251d;">
            <p style="margin:0;color:#08704d;font-weight:700;letter-spacing:.08em;">
              ZÉ REGISTRA® · PRIVACIDADE
            </p>
            <h1 style="font-size:28px;margin:22px 0 12px;">Confirme a exclusão dos seus dados</h1>
            <p>Recebemos um pedido para apagar seus dados de contato do nosso sistema.
               Este link é individual, pode ser usado uma única vez e expira em {horas}h.</p>
            <p style="margin:28px 0;">
              <a href="{html.escape(link, quote=True)}"
                 style="display:inline-block;background:#086044;color:#fff;text-decoration:none;
                        border-radius:10px;padding:14px 22px;font-weight:700;">
                Confirmar exclusão
              </a>
            </p>
            <p style="color:#607068;font-size:13px;">
              Se você não fez esse pedido, ignore esta mensagem -- nada será alterado.
            </p>
          </main>
        </body></html>
        """,
        subtype="html",
    )
    return mensagem


async def enviar_confirmacao_exclusao(destinatario: str, token: str) -> None:
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = _mensagem_confirmacao_exclusao(destinatario, token, settings)
    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, settings.smtp_max_attempts) + 1):
        try:
            await _enviar_smtp_contabilizado(mensagem, settings, "confirmacao_exclusao")
            return
        except Exception as exc:
            ultimo_erro = exc
            if erro_cota_diaria_email(exc):
                break
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
        await _registrar_email_rejeitado("confirmacao_exclusao", ultimo_erro)
        raise ultimo_erro


def _mensagem_passo_cadencia(
    destinatario: str,
    nome: str,
    titulo: str,
    corpo: str,
    rastreio_url: str,
    descadastro_url: str,
    settings: Settings,
) -> EmailMessage:
    nome_seguro = html.escape(nome or "")
    saudacao = f"Olá, {nome_seguro}." if nome_seguro else "Olá."
    corpo_seguro = html.escape(corpo).replace("\n", "<br>") if corpo else ""
    mensagem = EmailMessage()
    mensagem["Subject"] = titulo
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    mensagem.set_content(
        f"{saudacao}\n\n{corpo}\n\n-- \n{settings.email_from_name}\n\n"
        f"Não quer mais receber estes e-mails? Descadastre-se: {descadastro_url}"
    )
    mensagem.add_alternative(
        f"""
        <!doctype html>
        <html lang="pt-BR"><body style="margin:0;background:#f5f3eb;padding:28px;">
          <main style="max-width:600px;margin:auto;background:#fff;border:1px solid #d8ddd6;
                       border-radius:20px;padding:36px;font-family:Arial,sans-serif;color:#10251d;">
            <p style="margin:0;color:#08704d;font-weight:700;letter-spacing:.08em;">
              ZÉ REGISTRA®
            </p>
            <p style="margin-top:22px;">{saudacao}</p>
            <p>{corpo_seguro}</p>
            <p style="color:#607068;font-size:13px;margin-top:28px;">{html.escape(settings.email_from_name)}</p>
            <p style="color:#8b948c;font-size:11px;margin-top:18px;border-top:1px solid #e5e9e3;padding-top:14px;">
              Não quer mais receber estes e-mails?
              <a href="{html.escape(descadastro_url, quote=True)}" style="color:#607068;">Descadastre-se aqui</a>.
            </p>
          </main>
          <img src="{html.escape(rastreio_url, quote=True)}" width="1" height="1" alt="" style="display:none">
        </body></html>
        """,
        subtype="html",
    )
    return mensagem


async def enviar_passo_cadencia(
    destinatario: str, nome: str, titulo: str, corpo: str, rastreio_url: str, descadastro_url: str
) -> None:
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = _mensagem_passo_cadencia(destinatario, nome, titulo, corpo, rastreio_url, descadastro_url, settings)
    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, settings.smtp_max_attempts) + 1):
        try:
            await _enviar_smtp_contabilizado(mensagem, settings, "passo_cadencia")
            return
        except Exception as exc:
            ultimo_erro = exc
            if erro_cota_diaria_email(exc):
                break
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
        await _registrar_email_rejeitado("passo_cadencia", ultimo_erro)
        raise ultimo_erro


async def enviar_recuperacao_portal(destinatario: str, nome: str, token: str) -> None:
    """Envia recuperação do portal sem reutilizar o link do Centro de Operações."""
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = "Recuperação de acesso ao Portal do cliente — Zé Registra"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    link = f"{settings.app_public_url.rstrip('/')}/portal#recuperacao={quote(token, safe='')}"
    mensagem.set_content(
        f"Olá, {nome or 'cliente'}.\n\nAcesse o portal para redefinir seu acesso:\n{link}\n\nO link expira em 30 minutos e pode ser usado uma única vez."
    )
    try:
        await _enviar_smtp_contabilizado(mensagem, settings, "recuperacao_portal")
    except Exception as exc:
        await _registrar_email_rejeitado("recuperacao_portal", exc)
        raise


async def enviar_codigo_confirmacao_portal(destinatario: str, nome: str, codigo: str, descricao: str) -> None:
    """Segundo fator pra assinar proposta/documento pelo portal do cliente
    (Fase 13.2 da auditoria fina, 23/09/2026, decisão do usuário: mesmo
    padrão de dupla validação por e-mail do aceite público de proposta --
    ver enviar_codigo_confirmacao_proposta) -- propaga a exceção em vez de
    engolir a falha: se o código não sair, o cliente precisa ver isso na
    tela em vez de ficar esperando um e-mail que nunca chega."""
    settings = get_settings()
    if not settings.email_enabled:
        raise RuntimeError("Envio de e-mail não está habilitado nesta instalação")
    mensagem = EmailMessage()
    mensagem["Subject"] = f"Código de confirmação — {descricao}"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    mensagem.set_content(
        f"Olá, {nome or 'cliente'}.\n\n"
        f"Use o código abaixo para confirmar a assinatura de {descricao} no portal:\n\n"
        f"{codigo}\n\n"
        "O código vale por 15 minutos. Se você não solicitou esta assinatura, ignore esta mensagem."
    )
    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, settings.smtp_max_attempts) + 1):
        try:
            await _enviar_smtp_contabilizado(mensagem, settings, "codigo_confirmacao_portal")
            return
        except Exception as exc:
            ultimo_erro = exc
            if erro_cota_diaria_email(exc):
                break
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
        await _registrar_email_rejeitado("codigo_confirmacao_portal", ultimo_erro)
        raise ultimo_erro


async def enviar_alerta_novo_lead(
    nome: str, email: str, telefone: str, marca: str, origem: str, destinatario: str | None = None
) -> None:
    """Avisa a equipe de atendimento por e-mail quando um novo lead chega sem
    responsável (achado P0 da auditoria de Leads, 03/09/2026: o formulário
    genérico de captação não tinha nenhum alerta ativo, diferente do fluxo de
    pesquisa de marca). Silencioso se e-mail ou destinatário não configurados.

    Achado da Fase 15.2 (23/09/2026): "destinatario" é o e-mail configurado
    por organização (PoliticaCRM.email_alerta_leads). Achado P1 do Codex no
    PR #133: um fallback pra settings.equipe_atendimento_email (env var
    global do processo) recriava exatamente o vazamento entre tenants que
    esta fase corrige -- toda organização sem o campo configurado (o estado
    de toda organização já existente logo após a migration) mandaria dados
    de lead pra uma caixa de e-mail de OUTRO tenant. Sem destinatário
    configurado pra esta organização, o alerta simplesmente não dispara."""
    settings = get_settings()
    if not settings.email_enabled or not destinatario:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = f"Novo lead recebido: {nome}"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    linha_marca = f"\nMarca de interesse: {marca}" if marca else ""
    mensagem.set_content(
        f"Um novo lead acabou de ser recebido, ainda sem responsável.\n\n"
        f"Nome: {nome}\n"
        f"E-mail: {email}\n"
        f"Telefone: {telefone}\n"
        f"Origem: {origem}"
        f"{linha_marca}\n\n"
        "Acesse o Centro de Operações para assumir o atendimento."
    )
    try:
        await _enviar_smtp_contabilizado(mensagem, settings, "alerta_novo_lead")
    except Exception as exc:
        # Falha de e-mail não deve impedir a criação do lead nem derrubar a
        # requisição do cliente -- o painel continua sendo a fonte de verdade.
        await _registrar_email_rejeitado("alerta_novo_lead", exc)
        logger.exception("Falha ao enviar alerta de novo lead por e-mail")


async def enviar_alerta_lead_atribuido(
    nome_operador: str, email_operador: str, nome_lead: str, marca: str, lead_id: int
) -> None:
    """Avisa o operador quando uma oportunidade é atribuída a ele (achado P2 da
    auditoria de Leads, 03/09/2026). Silencioso se e-mail estiver desabilitado
    -- o painel continua sendo a fonte de verdade."""
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = f"Oportunidade atribuída a você: {nome_lead}"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = email_operador
    link = f"{settings.app_public_url.rstrip('/')}/admin/pesquisas?lead_id={lead_id}"
    linha_marca = f"\nMarca de interesse: {marca}" if marca else ""
    mensagem.set_content(
        f"Olá, {nome_operador or 'operador'}.\n\n"
        f"A oportunidade \"{nome_lead}\" foi atribuída a você.{linha_marca}\n\n"
        f"Acesse: {link}"
    )
    try:
        await _enviar_smtp_contabilizado(mensagem, settings, "alerta_lead_atribuido")
    except Exception as exc:
        await _registrar_email_rejeitado("alerta_lead_atribuido", exc)
        logger.exception("Falha ao enviar alerta de lead atribuído por e-mail")


async def enviar_alerta_atividades_atrasadas(
    nome_operador: str, email_operador: str, itens: list[tuple[str, str]]
) -> None:
    """Avisa o operador quando oportunidades sob sua responsabilidade ficam sem
    próxima ação (ou com ela vencida) -- achado P2 da auditoria de Leads
    (03/09/2026). Disparado pelo mesmo job semanal que já cria o lembrete
    interno (mesma idempotência: no máximo um aviso por lead por semana)."""
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = EmailMessage()
    plural = "s" if len(itens) != 1 else ""
    mensagem["Subject"] = f"{len(itens)} oportunidade{plural} parada{plural} — retomar contato"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = email_operador
    lista = "\n".join(f"- {nome}" + (f" ({marca})" if marca else "") for nome, marca in itens)
    mensagem.set_content(
        f"Olá, {nome_operador or 'operador'}.\n\n"
        f"As oportunidades abaixo, sob sua responsabilidade, estão sem próxima ação definida "
        f"ou com o prazo vencido:\n\n{lista}\n\n"
        "Acesse o Centro de Operações para planejar o próximo passo."
    )
    try:
        await _enviar_smtp_contabilizado(mensagem, settings, "alerta_atividades_atrasadas")
    except Exception as exc:
        await _registrar_email_rejeitado("alerta_atividades_atrasadas", exc)
        logger.exception("Falha ao enviar alerta de atividades atrasadas por e-mail")


async def enviar_alerta_nova_pesquisa(
    marca: str, nome_lead: str, empresa: str | None, destinatario: str | None = None
) -> None:
    """Avisa a equipe de atendimento por e-mail quando uma nova pesquisa chega.

    Silencioso se e-mail ou o destinatário não estiverem configurados: este alerta é um
    reforço da central de notificações do painel, não o único canal.

    Achado da Fase 15.2 (23/09/2026): mesmo raciocínio de
    enviar_alerta_novo_lead -- "destinatario" é o e-mail configurado por
    organização. Achado P1 do Codex no PR #133: nada de fallback pra
    settings.equipe_atendimento_email (env var global), senão qualquer
    organização sem o campo configurado mandaria dados de lead pra uma
    caixa de e-mail de outro tenant.
    """
    settings = get_settings()
    if not settings.email_enabled or not destinatario:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = f"Nova pesquisa recebida: {marca}"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    linha_empresa = f" ({empresa})" if empresa else ""
    mensagem.set_content(
        f"Uma nova pesquisa de marca acabou de ser recebida.\n\n"
        f"Marca: {marca}\n"
        f"Contato: {nome_lead}{linha_empresa}\n\n"
        "Acesse o Centro de Operações para acompanhar o lead."
    )
    try:
        await _enviar_smtp_contabilizado(mensagem, settings, "alerta_nova_pesquisa")
    except Exception as exc:
        # Falha de e-mail não deve impedir a criação da pesquisa nem derrubar a
        # requisição do cliente; a central de notificações do painel já cobre o alerta.
        await _registrar_email_rejeitado("alerta_nova_pesquisa", exc)
        logger.exception("Falha ao enviar alerta de nova pesquisa por e-mail")


async def enviar_proposta_email(destinatario: str, nome: str, link: str, pdf_bytes: bytes, numero: str) -> None:
    """Envia a proposta com link seguro e PDF anexado, quando SMTP estiver habilitado."""
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = f"Proposta de registro de marca {numero} - Zé Registra"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    mensagem.set_content(
        f"Olá, {nome or 'cliente'}.\n\n"
        "Sua proposta de registro de marca está disponível no link abaixo:\n\n"
        f"{link}\n\n"
        "O PDF da proposta também está anexado."
    )
    mensagem.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=f"proposta-{numero}.pdf")
    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, settings.smtp_max_attempts) + 1):
        try:
            await _enviar_smtp_contabilizado(mensagem, settings, "proposta")
            return
        except Exception as exc:
            ultimo_erro = exc
            if erro_cota_diaria_email(exc):
                break
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
        await _registrar_email_rejeitado("proposta", ultimo_erro)
        raise ultimo_erro


async def enviar_codigo_confirmacao_proposta(destinatario: str, nome: str, codigo: str, numero: str) -> None:
    """Segundo fator do aceite de proposta (dupla validação, orientação
    jurídica de 15/09/2026) -- propaga a exceção em vez de engolir a falha:
    se o código não sair, o cliente precisa ver isso na tela em vez de
    ficar esperando um e-mail que nunca chega."""
    settings = get_settings()
    if not settings.email_enabled:
        raise RuntimeError("Envio de e-mail não está habilitado nesta instalação")
    mensagem = EmailMessage()
    mensagem["Subject"] = f"Código de confirmação — Proposta {numero}"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    mensagem.set_content(
        f"Olá, {nome or 'cliente'}.\n\n"
        f"Use o código abaixo para confirmar o aceite da proposta {numero}:\n\n"
        f"{codigo}\n\n"
        "O código vale por 15 minutos. Se você não solicitou este aceite, ignore esta mensagem."
    )
    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, settings.smtp_max_attempts) + 1):
        try:
            await _enviar_smtp_contabilizado(mensagem, settings, "codigo_confirmacao_proposta")
            return
        except Exception as exc:
            ultimo_erro = exc
            if erro_cota_diaria_email(exc):
                break
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
        await _registrar_email_rejeitado("codigo_confirmacao_proposta", ultimo_erro)
        raise ultimo_erro


async def enviar_email_prospeccao_lead(
    destinatario: str, assunto: str, corpo: str, reply_to: str | None = None
) -> None:
    """Envia ao lead um e-mail comercial a partir do modelo configurado em
    Configuração > Modelo de e-mail (leads). Diferente dos alertas internos
    acima, é uma ação explícita do operador (botão "Enviar e-mail" no card do
    lead) -- por isso propaga a exceção em vez de engolir a falha: quem
    clicou precisa saber se não foi enviado."""
    settings = get_settings()
    if not settings.email_enabled:
        raise RuntimeError("Envio de e-mail não está habilitado nesta instalação")
    mensagem = EmailMessage()
    mensagem["Subject"] = assunto
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    if reply_to:
        mensagem["Reply-To"] = reply_to
    mensagem.set_content(corpo)
    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, settings.smtp_max_attempts) + 1):
        try:
            await _enviar_smtp_contabilizado(mensagem, settings, "prospeccao_lead")
            return
        except Exception as exc:
            ultimo_erro = exc
            if erro_cota_diaria_email(exc):
                break
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
        await _registrar_email_rejeitado("prospeccao_lead", ultimo_erro)
        raise ultimo_erro


async def enviar_alerta_prazo_juridico(destinatario: str, titulo: str, mensagem_texto: str) -> None:
    """Avisa por e-mail o responsável por um prazo jurídico vencido, próximo do
    vencimento ou escalonado.

    Achado 5.9 da auditoria (02/09/2026), Fase 8: o motor jurídico só criava um
    registro de ``NotificacaoJuridica`` no banco (a central de notificações do
    painel), mas nunca enviava nada de fato — quem não abrisse o painel nunca
    ficava sabendo. Silencioso se e-mail ou o destinatário não estiverem
    configurados, e não propaga falha de envio: a central de notificações do
    painel continua sendo o registro de referência, este e-mail é reforço.
    """
    settings = get_settings()
    if not settings.email_enabled or not destinatario:
        return
    link = f"{settings.app_public_url.rstrip('/')}/admin/operacao-juridica"
    mensagem = EmailMessage()
    mensagem["Subject"] = f"[Zé Registra] {titulo}"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    mensagem.set_content(
        f"{mensagem_texto}\n\nAcesse a Operação Jurídica para ver os detalhes e confirmar:\n{link}"
    )
    try:
        await _enviar_smtp_contabilizado(mensagem, settings, "alerta_prazo_juridico")
    except Exception as exc:
        await _registrar_email_rejeitado("alerta_prazo_juridico", exc)
        logger.exception("Falha ao enviar alerta de prazo jurídico por e-mail")


async def enviar_alerta_plataforma(codigo: str, severidade: str, mensagem_texto: str) -> None:
    """Avisa os admins por e-mail sobre um alerta de infraestrutura/plataforma
    (fila de falhas, RPI desatualizada, backup ausente, latência/erro de API
    etc -- achado FASE6-9 da auditoria, 04/09/2026).

    O registro em AlertaSistema (organizacao_id=None) é sempre a fonte de
    referência -- este e-mail é reforço best-effort, igual ao alerta de
    prazo jurídico: se o próprio SMTP estiver fora do ar (item 9g), o alerta
    continua visível no painel mesmo que este e-mail nunca chegue.
    """
    settings = get_settings()
    if not settings.email_enabled or not settings.admin_email:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = f"[Zé Registra] [{severidade.upper()}] {codigo}"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = settings.admin_email
    mensagem.set_content(mensagem_texto)
    try:
        await _enviar_smtp_contabilizado(mensagem, settings, "alerta_plataforma")
    except Exception as exc:
        await _registrar_email_rejeitado("alerta_plataforma", exc)
        logger.exception("Falha ao enviar alerta de plataforma por e-mail (codigo=%s)", codigo)

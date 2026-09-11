from __future__ import annotations

import asyncio
import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote

from app.settings import Settings, get_settings

logger = logging.getLogger("ze_registra.emailing")


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


def _enviar_smtp(mensagem: EmailMessage, settings: Settings) -> None:
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
            await asyncio.to_thread(_enviar_smtp, mensagem, settings)
            return
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
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
            await asyncio.to_thread(_enviar_smtp, mensagem, settings)
            return
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
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
            await asyncio.to_thread(_enviar_smtp, mensagem, settings)
            return
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
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
    await asyncio.to_thread(_enviar_smtp, mensagem, settings)


async def enviar_alerta_novo_lead(nome: str, email: str, telefone: str, marca: str, origem: str) -> None:
    """Avisa a equipe de atendimento por e-mail quando um novo lead chega sem
    responsável (achado P0 da auditoria de Leads, 03/09/2026: o formulário
    genérico de captação não tinha nenhum alerta ativo, diferente do fluxo de
    pesquisa de marca). Silencioso se e-mail ou destinatário não configurados."""
    settings = get_settings()
    if not settings.email_enabled or not settings.equipe_atendimento_email:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = f"Novo lead recebido: {nome}"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = settings.equipe_atendimento_email
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
        await asyncio.to_thread(_enviar_smtp, mensagem, settings)
    except Exception:
        # Falha de e-mail não deve impedir a criação do lead nem derrubar a
        # requisição do cliente -- o painel continua sendo a fonte de verdade.
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
        await asyncio.to_thread(_enviar_smtp, mensagem, settings)
    except Exception:
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
        await asyncio.to_thread(_enviar_smtp, mensagem, settings)
    except Exception:
        logger.exception("Falha ao enviar alerta de atividades atrasadas por e-mail")


async def enviar_alerta_nova_pesquisa(marca: str, nome_lead: str, empresa: str | None) -> None:
    """Avisa a equipe de atendimento por e-mail quando uma nova pesquisa chega.

    Silencioso se e-mail ou o destinatário não estiverem configurados: este alerta é um
    reforço da central de notificações do painel, não o único canal.
    """
    settings = get_settings()
    if not settings.email_enabled or not settings.equipe_atendimento_email:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = f"Nova pesquisa recebida: {marca}"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = settings.equipe_atendimento_email
    linha_empresa = f" ({empresa})" if empresa else ""
    mensagem.set_content(
        f"Uma nova pesquisa de marca acabou de ser recebida.\n\n"
        f"Marca: {marca}\n"
        f"Contato: {nome_lead}{linha_empresa}\n\n"
        "Acesse o Centro de Operações para acompanhar o lead."
    )
    try:
        await asyncio.to_thread(_enviar_smtp, mensagem, settings)
    except Exception:
        # Falha de e-mail não deve impedir a criação da pesquisa nem derrubar a
        # requisição do cliente; a central de notificações do painel já cobre o alerta.
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
            await asyncio.to_thread(_enviar_smtp, mensagem, settings)
            return
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
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
            await asyncio.to_thread(_enviar_smtp, mensagem, settings)
            return
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
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
        await asyncio.to_thread(_enviar_smtp, mensagem, settings)
    except Exception:
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
        await asyncio.to_thread(_enviar_smtp, mensagem, settings)
    except Exception:
        logger.exception("Falha ao enviar alerta de plataforma por e-mail (codigo=%s)", codigo)

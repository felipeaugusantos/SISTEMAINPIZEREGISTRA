"""Cadências reais por e-mail (Fase 9 do plano Leads/CRM, achados L5/L6).

Sem WhatsApp Business nesta fase -- só o canal "email" das cadências ganha
envio automático de verdade; os demais canais continuam apenas gerando o
LembreteCRM manual de sempre (app/api/leads.py::aplicar_cadencia_lead).

"enviado" é o máximo que este módulo garante -- SMTP puro não tem webhook de
provedor para confirmar entrega real na caixa do destinatário.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import hash_token
from app.emailing import enviar_passo_cadencia
from app.marca import marca_organizacao
from app.models import CadenciaPasso, EnvioCadenciaEmail, Lead, Organizacao
from app.settings import get_settings

logger = logging.getLogger("ze_registra.cadencia_email")

FUSO_COMERCIAL = ZoneInfo("America/Sao_Paulo")


def dentro_do_horario_comercial(momento_utc: datetime, inicio: int, fim: int) -> bool:
    """Dias úteis (segunda a sexta), no fuso America/Sao_Paulo."""
    local = momento_utc.astimezone(FUSO_COMERCIAL)
    if local.weekday() >= 5:
        return False
    return inicio <= local.hour < fim


async def _nome_escritorio(session: AsyncSession, organizacao_id: int) -> str | None:
    """Nome do escritório para o e-mail, ou None na organização padrão sem
    marca própria (e-mail como sempre foi). Falha na consulta não impede o
    envio."""
    try:
        org = (
            await session.execute(select(Organizacao).where(Organizacao.id == organizacao_id))
        ).scalar_one_or_none()
    except Exception:
        logger.exception("Falha ao resolver o nome do escritório para a cadência (org %s)", organizacao_id)
        return None
    if org is None:
        return None
    marca = marca_organizacao(org)
    return marca["nome"] if marca["propria"] else None


def gerar_token_rastreio() -> tuple[str, str]:
    """Devolve (token_bruto_para_o_link, hash_para_gravar_no_banco)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


async def processar_envios_cadencia_pendentes(session: AsyncSession) -> dict:
    """Job periódico do worker: dispara os passos de cadência (canal e-mail)
    cujo agendamento já venceu, respeitando o horário comercial."""
    settings = get_settings()
    agora = datetime.now(UTC)
    if not dentro_do_horario_comercial(
        agora, settings.cadencia_email_horario_inicio, settings.cadencia_email_horario_fim
    ):
        return {"enviados": 0, "falhas": 0, "motivo": "fora_do_horario_comercial"}

    pendentes = (
        (
            await session.execute(
                select(EnvioCadenciaEmail)
                .where(
                    EnvioCadenciaEmail.status == "pendente",
                    EnvioCadenciaEmail.agendado_para <= agora,
                )
                .options(selectinload(EnvioCadenciaEmail.passo))
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    enviados = 0
    falhas = 0
    # Achado 17.4: o worker gravava o total agregado de TODAS as
    # organizações num AlertaSistema fixo da organização 1 (visível ao
    # comercial dela). Com a quebra por organização, cada uma recebe só os
    # próprios números.
    por_organizacao: dict[int, dict[str, int]] = {}

    def _contar(organizacao_id: int, chave: str) -> None:
        contagem = por_organizacao.setdefault(organizacao_id, {"enviados": 0, "falhas": 0})
        contagem[chave] += 1

    # Fase 19.3 (white-label): remetente e assinatura com o nome do
    # escritório de cada envio; uma consulta por organização no lote.
    nomes_escritorio: dict[int, str | None] = {}
    for envio in pendentes:
        lead = await session.get(Lead, envio.lead_id)
        if lead is None or lead.anonimizado_em is not None or lead.arquivado_em is not None or not lead.email:
            envio.status = "falhou"
            envio.ultimo_erro = "lead indisponível, anonimizado, arquivado ou sem e-mail"
            falhas += 1
            _contar(envio.organizacao_id, "falhas")
            continue
        # Achado da auditoria completa do CRM (06/09/2026): antes não havia
        # como um titular parar de receber cadência sem responder o e-mail
        # (que só pausa os envios já agendados) nem pedir exclusão total dos
        # dados -- opt-out específico, sem apagar nada.
        if lead.cadencia_opt_out_em is not None:
            envio.status = "pausado"
            envio.pausado_em = agora
            envio.ultimo_erro = "lead descadastrado da cadência"
            continue
        passo = envio.passo
        if passo is None:
            envio.status = "falhou"
            envio.ultimo_erro = "passo da cadência não existe mais"
            falhas += 1
            _contar(envio.organizacao_id, "falhas")
            continue
        # O token bruto só existe agora -- gerar no agendamento seria inútil
        # (nada pode reconstruí-lo a partir do hash mais tarde). Reaproveitado
        # também para o link de descadastro (mesmo token, mesma política RLS
        # de leitura pré-tenant já existente -- sem coluna nem policy nova).
        token, token_hash = gerar_token_rastreio()
        rastreio_url = f"{settings.app_public_url.rstrip('/')}/v1/cadencias/rastreio/{token}.gif"
        descadastro_url = f"{settings.app_public_url.rstrip('/')}/v1/cadencias/descadastrar/{token}"
        try:
            if envio.organizacao_id not in nomes_escritorio:
                nomes_escritorio[envio.organizacao_id] = await _nome_escritorio(session, envio.organizacao_id)
            await enviar_passo_cadencia(
                lead.email,
                lead.nome,
                passo.titulo,
                passo.descricao or "",
                rastreio_url,
                descadastro_url,
                organizacao_nome=nomes_escritorio[envio.organizacao_id],
            )
            envio.status = "enviado"
            envio.enviado_em = agora
            envio.rastreio_token_hash = token_hash
            enviados += 1
            _contar(envio.organizacao_id, "enviados")
        except Exception as exc:
            envio.tentativas += 1
            envio.ultimo_erro = str(exc)[:300]
            if envio.tentativas >= settings.cadencia_email_max_tentativas:
                envio.status = "falhou"
            falhas += 1
            _contar(envio.organizacao_id, "falhas")
            logger.warning("Falha ao enviar passo de cadência (envio_id=%s): %s", envio.id, exc)
    return {"enviados": enviados, "falhas": falhas, "por_organizacao": por_organizacao}


async def registrar_abertura(session: AsyncSession, token: str) -> None:
    """Chamado pelo endpoint público do pixel de rastreio (recebe o token bruto
    da URL, sem tenant resolvido ainda -- mesmo padrão de
    leads_propostas.py::_proposta_por_token). Marca só a primeira abertura --
    reaberturas não sobrescrevem o timestamp original. Não commita: quem
    chama decide."""
    from app.tenancy import aplicar_contexto_tenant

    envio = (
        await session.execute(select(EnvioCadenciaEmail).where(EnvioCadenciaEmail.rastreio_token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if envio is None:
        return
    await aplicar_contexto_tenant(session, envio.organizacao_id)
    if envio.aberto_em is None:
        envio.aberto_em = datetime.now(UTC)


async def processar_descadastro_cadencia(session: AsyncSession, token: str) -> bool:
    """Chamado pelo link público de descadastro no rodapé do e-mail de
    cadência (mesmo token do pixel de rastreio -- mesma política RLS de
    leitura pré-tenant já existente em envios_cadencia_email, sem coluna
    nem policy nova). Marca o lead como descadastrado (não apaga nada,
    diferente da exclusão via app.api.privacidade) e cancela os envios
    ainda pendentes dessa cadência. Devolve True se encontrou o lead
    (mesmo que já estivesse descadastrado antes -- idempotente).
    Não commita: quem chama decide."""
    from app.tenancy import aplicar_contexto_tenant

    envio = (
        await session.execute(select(EnvioCadenciaEmail).where(EnvioCadenciaEmail.rastreio_token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if envio is None:
        return False
    await aplicar_contexto_tenant(session, envio.organizacao_id)
    lead = await session.get(Lead, envio.lead_id)
    if lead is None:
        return False
    if lead.cadencia_opt_out_em is None:
        lead.cadencia_opt_out_em = datetime.now(UTC)
    await pausar_envios_pendentes_do_lead(session, envio.organizacao_id, envio.lead_id)
    return True


async def pausar_envios_pendentes_do_lead(session: AsyncSession, organizacao_id: int, lead_id: int) -> int:
    """Resposta do titular -- para a cadência daquele lead (achado L6: pausa ao
    responder). Não cancela envios já enviados, só os que ainda não saíram."""
    agora = datetime.now(UTC)
    resultado = await session.execute(
        update(EnvioCadenciaEmail)
        .where(
            EnvioCadenciaEmail.organizacao_id == organizacao_id,
            EnvioCadenciaEmail.lead_id == lead_id,
            EnvioCadenciaEmail.status == "pendente",
        )
        .values(status="pausado", pausado_em=agora, respondido_em=agora)
    )
    return resultado.rowcount


def montar_envio_pendente(
    *, organizacao_id: int, lead_id: int, cadencia_id: int, passo: CadenciaPasso, agora: datetime
) -> dict:
    """Monta os valores para inserir um EnvioCadenciaEmail (canal e-mail de uma
    cadência), agendado mas ainda não enviado. Sem token de rastreio ainda --
    só é gerado no envio de fato (processar_envios_cadencia_pendentes)."""
    return {
        "organizacao_id": organizacao_id,
        "lead_id": lead_id,
        "cadencia_id": cadencia_id,
        "passo_id": passo.id,
        "agendado_para": agora + timedelta(days=passo.dia),
    }

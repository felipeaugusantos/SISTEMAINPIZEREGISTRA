"""Política e simulação de retenção, sempre sem descarte automático."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BloqueioRetencao,
    ContratacaoServico,
    DocumentoLead,
    Lead,
    Organizacao,
    PoliticaRetencao,
    ProcessoMonitorado,
    StatusLead,
)

CATEGORIA_LEAD = "lead"
STATUS_CONTRATO_INATIVOS = ("cancelada", "cancelado", "encerrada", "encerrado", "inativa", "inativo")


async def politica_retencao_vigente(
    session: AsyncSession,
    organizacao: Organizacao,
    *,
    categoria: str = CATEGORIA_LEAD,
    agora: datetime | None = None,
) -> PoliticaRetencao | None:
    agora = agora or datetime.now(UTC)
    return (
        await session.execute(
            select(PoliticaRetencao)
            .where(
                PoliticaRetencao.organizacao_id == organizacao.id,
                PoliticaRetencao.categoria == categoria,
                PoliticaRetencao.ativo.is_(True),
                PoliticaRetencao.vigencia_em <= agora,
            )
            .order_by(PoliticaRetencao.vigencia_em.desc(), PoliticaRetencao.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def prazo_retencao_vigente(
    session: AsyncSession,
    organizacao: Organizacao,
    *,
    categoria: str = CATEGORIA_LEAD,
    agora: datetime | None = None,
) -> int:
    politica = await politica_retencao_vigente(
        session,
        organizacao,
        categoria=categoria,
        agora=agora,
    )
    return politica.prazo_dias if politica is not None else organizacao.retencao_dados_dias


def _processo_ativo(organizacao_id: int):
    return exists(
        select(ProcessoMonitorado.id).where(
            ProcessoMonitorado.organizacao_id == organizacao_id,
            ProcessoMonitorado.lead_id == Lead.id,
            ProcessoMonitorado.status == "ativo",
        )
    )


def _contrato_ativo(organizacao_id: int):
    return exists(
        select(ContratacaoServico.id).where(
            ContratacaoServico.organizacao_id == organizacao_id,
            ContratacaoServico.lead_id == Lead.id,
            ContratacaoServico.status.notin_(STATUS_CONTRATO_INATIVOS),
        )
    )


def _documento_obrigatorio(organizacao_id: int):
    return exists(
        select(DocumentoLead.id).where(
            DocumentoLead.organizacao_id == organizacao_id,
            DocumentoLead.lead_id == Lead.id,
            DocumentoLead.obrigatorio.is_(True),
        )
    )


def _legal_hold(organizacao_id: int):
    return exists(
        select(BloqueioRetencao.id).where(
            BloqueioRetencao.organizacao_id == organizacao_id,
            BloqueioRetencao.categoria == CATEGORIA_LEAD,
            BloqueioRetencao.recurso_id == Lead.id,
            BloqueioRetencao.ativo.is_(True),
        )
    )


async def motivos_bloqueio_lead(session: AsyncSession, lead: Lead) -> list[str]:
    """Retorna motivos conservadores que impedem o descarte de um lead."""

    motivos: list[str] = []
    if lead.status != StatusLead.DESCARTADO:
        motivos.append("atendimento_ativo")
    verificacoes = (
        ("processo_ativo", _processo_ativo(lead.organizacao_id)),
        ("contrato_ativo", _contrato_ativo(lead.organizacao_id)),
        ("documento_obrigacao_juridica", _documento_obrigatorio(lead.organizacao_id)),
        ("legal_hold", _legal_hold(lead.organizacao_id)),
    )
    for motivo, condicao in verificacoes:
        presente = (
            await session.execute(select(func.count()).select_from(Lead).where(Lead.id == lead.id, condicao))
        ).scalar_one()
        if presente:
            motivos.append(motivo)
    return motivos


async def simular_retencao_leads(
    session: AsyncSession,
    organizacao: Organizacao,
    *,
    prazo_dias: int | None = None,
    agora: datetime | None = None,
    limite_amostra: int = 200,
    deslocamento: int = 0,
) -> dict:
    """Calcula impacto sem modificar qualquer registro."""

    agora = agora or datetime.now(UTC)
    prazo = prazo_dias or await prazo_retencao_vigente(session, organizacao, agora=agora)
    limite = agora - timedelta(days=prazo)
    base = (
        Lead.organizacao_id == organizacao.id,
        Lead.criado_em < limite,
        Lead.anonimizado_em.is_(None),
    )

    total, mais_antigo = (
        await session.execute(select(func.count(), func.min(Lead.criado_em)).select_from(Lead).where(*base))
    ).one()
    por_status = {
        str(status.value if isinstance(status, StatusLead) else status): quantidade
        for status, quantidade in (
            await session.execute(
                select(Lead.status, func.count()).where(*base).group_by(Lead.status).order_by(Lead.status)
            )
        ).all()
    }

    bloqueadores = {
        "atendimento_ativo": Lead.status != StatusLead.DESCARTADO,
        "processo_ativo": _processo_ativo(organizacao.id),
        "contrato_ativo": _contrato_ativo(organizacao.id),
        "documento_obrigacao_juridica": _documento_obrigatorio(organizacao.id),
        "legal_hold": _legal_hold(organizacao.id),
    }
    contagens_bloqueio: dict[str, int] = {}
    for nome, condicao in bloqueadores.items():
        contagens_bloqueio[nome] = (
            await session.execute(select(func.count()).select_from(Lead).where(*base, condicao))
        ).scalar_one()

    elegivel = (
        await session.execute(
            select(func.count())
            .select_from(Lead)
            .where(
                *base,
                Lead.status == StatusLead.DESCARTADO,
                ~_processo_ativo(organizacao.id),
                ~_contrato_ativo(organizacao.id),
                ~_documento_obrigatorio(organizacao.id),
                ~_legal_hold(organizacao.id),
            )
        )
    ).scalar_one()

    amostra_ids = list(
        (
            await session.execute(
                select(Lead.id)
                .where(*base)
                .order_by(Lead.criado_em.asc(), Lead.id.asc())
                .limit(limite_amostra)
                .offset(deslocamento)
            )
        ).scalars()
    )
    return {
        "modo": "simulacao",
        "executou_descarte": False,
        "categoria": CATEGORIA_LEAD,
        "prazo_dias": prazo,
        "data_corte": limite,
        "total_afetado": total,
        "registro_mais_antigo": mais_antigo,
        "categorias": {CATEGORIA_LEAD: total},
        "por_status": por_status,
        "bloqueios": contagens_bloqueio,
        "elegiveis_revisao_humana": elegivel,
        "amostra_ids": amostra_ids,
        "amostra_limitada": total > deslocamento + len(amostra_ids),
        "paginacao": {
            "limite": limite_amostra,
            "deslocamento": deslocamento,
            "proximo_deslocamento": (
                deslocamento + len(amostra_ids) if total > deslocamento + len(amostra_ids) else None
            ),
        },
    }

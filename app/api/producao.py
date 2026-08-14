from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import (
    AvaliacaoRiscoMarca,
    EventoAuditoria,
    EventoOperacional,
    PesquisaMarca,
    VersaoRelatorioMarca,
)
from app.schemas import (
    EventoAuditoriaResponse,
    ProducaoAdminResponse,
)
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin/producao", tags=["governança de produção"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("production.view"))]


async def _resumo(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    limite_auditoria: int = 10,
    deslocamento_auditoria: int = 0,
) -> ProducaoAdminResponse:
    settings = get_settings()
    desde = datetime.now(UTC) - timedelta(hours=24)
    operacional = (
        await session.execute(
            select(
                func.count(),
                func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0)),
                func.avg(EventoOperacional.duracao_ms),
                func.percentile_cont(0.95).within_group(EventoOperacional.duracao_ms),
                func.max(EventoOperacional.duracao_ms),
            ).where(EventoOperacional.criado_em >= desde)
        )
    ).one()
    if not usuario.superadmin:
        operacional = (0, 0, 0, 0, 0)
    comparacao = (
        await session.execute(
            select(
                func.count(),
                func.sum(
                    case(
                        (
                            AvaliacaoRiscoMarca.nivel != AvaliacaoRiscoMarca.nivel_humano,
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(
                AvaliacaoRiscoMarca.nivel_humano.is_not(None),
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).one()
    versoes = (
        await session.execute(
            select(
                func.count(),
                func.count(distinct(VersaoRelatorioMarca.pesquisa_id)),
            )
            .join(PesquisaMarca, PesquisaMarca.id == VersaoRelatorioMarca.pesquisa_id)
            .where(PesquisaMarca.organizacao_id == usuario.organizacao_id)
        )
    ).one()
    auditoria_total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(EventoAuditoria)
                .where(EventoAuditoria.organizacao_id == usuario.organizacao_id)
            )
        ).scalar_one()
    )
    auditoria = (
        (
            await session.execute(
                select(EventoAuditoria)
                .where(EventoAuditoria.organizacao_id == usuario.organizacao_id)
                .order_by(EventoAuditoria.criado_em.desc(), EventoAuditoria.id.desc())
                .limit(limite_auditoria)
                .offset(deslocamento_auditoria)
            )
        )
        .scalars()
        .all()
    )
    requisicoes = int(operacional[0] or 0)
    erros = int(operacional[1] or 0)
    avaliadas = int(comparacao[0] or 0)
    divergencias = int(comparacao[1] or 0)
    return ProducaoAdminResponse(
        ambiente=settings.app_env,
        requisicoes_24h=requisicoes,
        erros_24h=erros,
        taxa_erros_24h=erros / requisicoes if requisicoes else 0,
        duracao_media_ms_24h=float(operacional[2] or 0),
        duracao_p95_ms_24h=float(operacional[3] or 0),
        duracao_maxima_ms_24h=int(operacional[4] or 0),
        avaliacoes_humanas=avaliadas,
        divergencias_humanas=divergencias,
        taxa_divergencia=divergencias / avaliadas if avaliadas else 0,
        relatorios_versionados=int(versoes[0] or 0),
        pesquisas_com_versao=int(versoes[1] or 0),
        auditoria_total=auditoria_total,
        auditoria_limite=limite_auditoria,
        auditoria_deslocamento=deslocamento_auditoria,
        auditoria=[
            EventoAuditoriaResponse(
                request_id=item.request_id,
                actor_id=item.actor_id,
                ator=item.ator,
                acao=item.acao,
                recurso=item.recurso,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                sucesso=item.sucesso,
                status_http=item.status_http,
                before_state=item.before_state,
                after_state=item.after_state,
                criado_em=item.criado_em,
            )
            for item in auditoria
        ],
    )


@router.get("", response_model=ProducaoAdminResponse)
async def obter_producao(
    session: SessionDep,
    usuario: AdminDep,
    limite_auditoria: Annotated[int, Query(ge=1, le=100)] = 10,
    deslocamento_auditoria: Annotated[int, Query(ge=0)] = 0,
) -> ProducaoAdminResponse:
    resposta = await _resumo(
        session,
        usuario,
        limite_auditoria=limite_auditoria,
        deslocamento_auditoria=deslocamento_auditoria,
    )
    if not usuario.pode("audit.view"):
        resposta.auditoria = []
        resposta.auditoria_total = 0
        resposta.auditoria_deslocamento = 0
    return resposta

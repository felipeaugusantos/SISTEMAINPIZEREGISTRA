from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import (
    AfinidadeClasse,
    AvaliacaoRiscoMarca,
    Lead,
    MarcaAltoRenome,
    Movimentacao,
    PesquisaMarca,
    StatusLead,
)
from app.schemas import AdminResumoResponse

router = APIRouter(prefix="/v1/admin", tags=["painel administrativo"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("dashboard.view"))]


@router.get("/resumo", response_model=AdminResumoResponse)
async def obter_resumo(session: SessionDep, usuario: AdminDep) -> AdminResumoResponse:
    organizacao_id = getattr(usuario, "organizacao_id", 1)
    metricas = (
        await session.execute(
            select(
                select(func.count()).select_from(Lead).where(Lead.organizacao_id == organizacao_id).scalar_subquery(),
                (
                    select(func.count())
                    .select_from(Lead)
                    .where(Lead.status == StatusLead.NOVO, Lead.organizacao_id == organizacao_id)
                    .scalar_subquery()
                ),
                select(func.count())
                .select_from(PesquisaMarca)
                .where(PesquisaMarca.organizacao_id == organizacao_id)
                .scalar_subquery(),
                select(func.max(Movimentacao.numero_rpi)).scalar_subquery(),
                (
                    select(func.count())
                    .select_from(MarcaAltoRenome)
                    .where(MarcaAltoRenome.vigente.is_(True))
                    .scalar_subquery()
                ),
                (
                    select(func.count())
                    .select_from(AfinidadeClasse)
                    .where(AfinidadeClasse.status_revisao == "pendente")
                    .scalar_subquery()
                ),
                select(func.count())
                .select_from(AvaliacaoRiscoMarca)
                .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
                .where(PesquisaMarca.organizacao_id == organizacao_id)
                .scalar_subquery(),
                (
                    select(func.count())
                    .select_from(AvaliacaoRiscoMarca)
                    .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
                    .where(
                        AvaliacaoRiscoMarca.nivel.in_(["alto", "critico"]),
                        PesquisaMarca.organizacao_id == organizacao_id,
                    )
                    .scalar_subquery()
                ),
                (
                    select(func.count())
                    .select_from(AvaliacaoRiscoMarca)
                    .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
                    .where(
                        AvaliacaoRiscoMarca.nivel_humano.is_(None),
                        PesquisaMarca.organizacao_id == organizacao_id,
                    )
                    .scalar_subquery()
                ),
            )
        )
    ).one()
    return AdminResumoResponse(
        leads_total=metricas[0],
        leads_novos=metricas[1],
        pesquisas_total=metricas[2],
        ultima_rpi=metricas[3],
        alto_renome_vigentes=metricas[4],
        afinidades_pendentes=metricas[5],
        riscos_calculados=metricas[6],
        riscos_elevados=metricas[7],
        riscos_pendentes_revisao=metricas[8],
    )

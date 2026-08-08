from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AcaoAdminDep, UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import AfinidadeClasse, MarcaAltoRenome
from app.schemas import (
    AfinidadeClasseResponse,
    AfinidadeRevisaoUpdate,
    Fase2AdminResponse,
)

router = APIRouter(prefix="/v1/admin/fase2", tags=["validação da fase 2"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("validation.view"))]
WriteDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("validation.review"))]


@router.get("", response_model=Fase2AdminResponse)
async def obter_fase2(session: SessionDep, _: AdminDep) -> Fase2AdminResponse:
    afinidades = (
        (
            await session.execute(
                select(AfinidadeClasse).order_by(
                    AfinidadeClasse.status_revisao,
                    AfinidadeClasse.classe_origem,
                    AfinidadeClasse.classe_destino,
                )
            )
        )
        .scalars()
        .all()
    )
    contagens = {status: 0 for status in ("pendente", "aprovada", "rejeitada")}
    for afinidade in afinidades:
        contagens[afinidade.status_revisao] = contagens.get(afinidade.status_revisao, 0) + 1

    alto_renome_vigentes = (
        await session.execute(
            select(func.count())
            .select_from(MarcaAltoRenome)
            .where(MarcaAltoRenome.vigente.is_(True))
        )
    ).scalar_one()
    alto_renome_atualizado_em = (
        await session.execute(select(func.max(MarcaAltoRenome.sincronizado_em)))
    ).scalar_one()
    return Fase2AdminResponse(
        alto_renome_vigentes=alto_renome_vigentes,
        alto_renome_atualizado_em=alto_renome_atualizado_em,
        afinidades_pendentes=contagens["pendente"],
        afinidades_aprovadas=contagens["aprovada"],
        afinidades_rejeitadas=contagens["rejeitada"],
        afinidades=[AfinidadeClasseResponse.model_validate(item) for item in afinidades],
    )


@router.patch(
    "/afinidades/{afinidade_id}",
    response_model=AfinidadeClasseResponse,
)
async def revisar_afinidade(
    afinidade_id: int,
    dados: AfinidadeRevisaoUpdate,
    session: SessionDep,
    _: WriteDep,
    _limite: AcaoAdminDep,
) -> AfinidadeClasse:
    afinidade = await session.get(AfinidadeClasse, afinidade_id)
    if afinidade is None:
        raise HTTPException(status_code=404, detail="Relação de classes não encontrada")
    afinidade.status_revisao = dados.status_revisao
    afinidade.revisor = dados.revisor
    afinidade.observacoes_revisao = dados.observacoes_revisao
    afinidade.revisado_em = datetime.now(UTC)
    await session.commit()
    await session.refresh(afinidade)
    return afinidade

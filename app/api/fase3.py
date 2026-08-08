from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AcaoAdminDep, UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import AvaliacaoRiscoMarca, Lead, PesquisaMarca
from app.schemas import (
    AvaliacaoHumanaRiscoUpdate,
    AvaliacaoRiscoAdminItem,
    Fase3AdminResponse,
)
from app.trademarks.risk import MODO_MOTOR, VERSAO_MOTOR

router = APIRouter(prefix="/v1/admin/fase3", tags=["motor de risco em modo sombra"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("risk.view"))]
WriteDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("risk.review"))]
Limite = Annotated[int, Query(ge=1, le=200)]


def _item(
    avaliacao: AvaliacaoRiscoMarca,
    pesquisa: PesquisaMarca,
    lead: Lead,
) -> AvaliacaoRiscoAdminItem:
    concordancia = (
        avaliacao.nivel == avaliacao.nivel_humano if avaliacao.nivel_humano is not None else None
    )
    return AvaliacaoRiscoAdminItem(
        id=avaliacao.id,
        pesquisa_id=pesquisa.id,
        marca=pesquisa.marca,
        atividade=pesquisa.atividade or "Não informada",
        contato_nome=lead.nome,
        empresa=lead.empresa,
        pontuacao=avaliacao.pontuacao,
        nivel=avaliacao.nivel,
        versao_motor=avaliacao.versao_motor,
        modo=avaliacao.modo,
        principais_conflitos=avaliacao.principais_conflitos,
        regras_aplicadas=avaliacao.regras_aplicadas,
        calculado_em=avaliacao.calculado_em,
        nivel_humano=avaliacao.nivel_humano,
        avaliador=avaliacao.avaliador,
        observacoes_humanas=avaliacao.observacoes_humanas,
        avaliado_em=avaliacao.avaliado_em,
        concordancia=concordancia,
    )


@router.get("", response_model=Fase3AdminResponse)
async def listar_avaliacoes(
    session: SessionDep,
    usuario: AdminDep,
    limite: Limite = 100,
) -> Fase3AdminResponse:
    linhas = (
        await session.execute(
            select(AvaliacaoRiscoMarca, PesquisaMarca, Lead)
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .join(Lead, Lead.id == PesquisaMarca.lead_id)
            .where(PesquisaMarca.organizacao_id == usuario.organizacao_id)
            .order_by(AvaliacaoRiscoMarca.calculado_em.desc())
            .limit(limite)
        )
    ).all()
    itens = [_item(avaliacao, pesquisa, lead) for avaliacao, pesquisa, lead in linhas]
    total = (
        await session.execute(select(func.count()).select_from(AvaliacaoRiscoMarca).join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id).where(PesquisaMarca.organizacao_id == usuario.organizacao_id))
    ).scalar_one()
    total_avaliadas = (
        await session.execute(
            select(func.count())
            .select_from(AvaliacaoRiscoMarca)
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(AvaliacaoRiscoMarca.nivel_humano.is_not(None), PesquisaMarca.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one()
    total_concordantes = (
        await session.execute(
            select(func.count())
            .select_from(AvaliacaoRiscoMarca)
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(
                AvaliacaoRiscoMarca.nivel_humano.is_not(None),
                AvaliacaoRiscoMarca.nivel == AvaliacaoRiscoMarca.nivel_humano,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one()
    taxa = total_concordantes / total_avaliadas if total_avaliadas else None
    return Fase3AdminResponse(
        total_calculadas=total,
        total_avaliadas_humanamente=total_avaliadas,
        total_concordantes=total_concordantes,
        taxa_concordancia=taxa,
        modo=MODO_MOTOR,
        versao_motor=VERSAO_MOTOR,
        itens=itens,
    )


@router.patch(
    "/avaliacoes/{avaliacao_id}",
    response_model=AvaliacaoRiscoAdminItem,
)
async def registrar_avaliacao_humana(
    avaliacao_id: int,
    dados: AvaliacaoHumanaRiscoUpdate,
    session: SessionDep,
    usuario: WriteDep,
    _limite: AcaoAdminDep,
) -> AvaliacaoRiscoAdminItem:
    linha = (await session.execute(
        select(AvaliacaoRiscoMarca, PesquisaMarca, Lead)
        .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
        .join(Lead, Lead.id == PesquisaMarca.lead_id)
        .where(AvaliacaoRiscoMarca.id == avaliacao_id, PesquisaMarca.organizacao_id == usuario.organizacao_id)
    )).first()
    if linha is None:
        raise HTTPException(status_code=404, detail="Avaliação de risco não encontrada")
    avaliacao, pesquisa, lead = linha

    avaliacao.nivel_humano = dados.nivel_humano
    avaliacao.avaliador = dados.avaliador
    avaliacao.observacoes_humanas = dados.observacoes_humanas
    avaliacao.avaliado_em = datetime.now(UTC)
    await session.commit()
    await session.refresh(avaliacao)
    return _item(avaliacao, pesquisa, lead)

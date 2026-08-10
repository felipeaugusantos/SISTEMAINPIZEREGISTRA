from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pesquisas import gerar_resumo_pesquisa
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import Lead, PesquisaMarca, StatusLead, TipoProcesso
from app.schemas import PesquisaMarcaCriada, ResumoPublicoMarcaResponse

router = APIRouter(prefix="/v1/admin/consulta", tags=["consulta interna"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
# Consultar é uma ação de operador; reusa a permissão de leads (quem opera leads pesquisa).
OperadorDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]


class ConsultaOperadorInput(BaseModel):
    marca: str = Field(min_length=2, max_length=200)
    atividade: str = Field(min_length=3, max_length=500)
    nome: str = Field(default="Consulta operacional", max_length=150)
    empresa: str | None = Field(default=None, max_length=200)
    email: str = Field(default="", max_length=254)
    telefone: str = Field(default="", max_length=30)


@router.post("", response_model=PesquisaMarcaCriada, status_code=status.HTTP_201_CREATED)
async def criar_consulta(
    dados: ConsultaOperadorInput, session: SessionDep, operador: OperadorDep
) -> PesquisaMarcaCriada:
    lead = Lead(
        organizacao_id=operador.organizacao_id,
        nome=dados.nome.strip() or "Consulta operacional",
        empresa=(dados.empresa or None),
        email=dados.email.strip(),
        telefone=dados.telefone.strip(),
        marca=dados.marca,
        atividade=dados.atividade,
        origem="operador",
        tipo_interesse=TipoProcesso.MARCA,
        aceite_privacidade=True,
        aceite_marketing=False,
        responsavel_id=operador.id,
        status=StatusLead.NOVO,
    )
    session.add(lead)
    await session.flush()
    pesquisa = PesquisaMarca(
        organizacao_id=operador.organizacao_id,
        lead_id=lead.id,
        marca=dados.marca,
        atividade=dados.atividade,
        tipo_pesquisa="completa",
        classe_nice=None,
    )
    session.add(pesquisa)
    await session.commit()
    await session.refresh(pesquisa)
    return PesquisaMarcaCriada(id=pesquisa.id, relatorio_url=f"/admin/consulta/{pesquisa.id}")


@router.get("/{pesquisa_id}/relatorio", response_model=ResumoPublicoMarcaResponse)
async def relatorio_consulta(
    pesquisa_id: str, session: SessionDep, operador: OperadorDep
) -> ResumoPublicoMarcaResponse:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca)
            .where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == operador.organizacao_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Consulta não encontrada")
    return await gerar_resumo_pesquisa(session, pesquisa)

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pesquisas import detectar_pesquisa_duplicada, gerar_resumo_pesquisa
from app.auth import UsuarioAutenticado, exigir_permissao
from app.crm import buscar_lead_ativo_por_email, obter_ou_criar_empresa
from app.database import get_session
from app.models import (
    Contato,
    Lead,
    PesquisaMarca,
    SolicitacaoExclusaoPesquisa,
    StatusLead,
    TipoProcesso,
)
from app.schemas import PesquisaMarcaCriada, ResumoPublicoMarcaResponse

router = APIRouter(prefix="/v1/admin/consulta", tags=["consulta interna"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
OperadorDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]


class ConsultaOperadorInput(BaseModel):
    marca: str = Field(min_length=2, max_length=200)
    atividade: str | None = Field(default=None, max_length=500)
    nome: str = Field(default="Consulta operacional", max_length=150)
    empresa: str | None = Field(default=None, max_length=200)
    email: str = Field(default="", max_length=254)
    telefone: str = Field(default="", max_length=30)

    @field_validator("marca")
    @classmethod
    def limpar_marca(cls, valor: str) -> str:
        return valor.strip()

    @field_validator("atividade")
    @classmethod
    def limpar_atividade(cls, valor: str | None) -> str | None:
        return (valor or "").strip() or None


@router.post("", response_model=PesquisaMarcaCriada, status_code=status.HTTP_201_CREATED)
async def criar_consulta(
    dados: ConsultaOperadorInput, session: SessionDep, operador: OperadorDep
) -> PesquisaMarcaCriada:
    empresa = await obter_ou_criar_empresa(session, operador.organizacao_id, dados.empresa)
    email = dados.email.strip().lower()
    lead = await buscar_lead_ativo_por_email(session, operador.organizacao_id, email)
    if lead is not None and lead.contato_id is not None:
        with session.no_autoflush:
            contato_valido = (
                await session.execute(
                    select(Contato.id).where(
                        Contato.id == lead.contato_id,
                        Contato.organizacao_id == operador.organizacao_id,
                        Contato.empresa_id == (empresa.id if empresa else None),
                    )
                )
            ).scalar_one_or_none()
        if contato_valido is None:
            lead.contato_id = None
    if email and lead is None:
        lead = Lead(
            organizacao_id=operador.organizacao_id,
            empresa_id=empresa.id if empresa else None,
            nome=dados.nome.strip() or "Consulta operacional",
            empresa=empresa.nome if empresa else None,
            email=email,
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
    elif lead is not None:
        lead.nome = dados.nome.strip() or lead.nome
        lead.telefone = dados.telefone.strip() or lead.telefone
        if empresa is not None:
            lead.empresa_id = empresa.id
            lead.empresa = empresa.nome
        lead.marca = dados.marca
        if dados.atividade is not None:
            lead.atividade = dados.atividade
        lead.responsavel_id = lead.responsavel_id or operador.id

    original = await detectar_pesquisa_duplicada(
        session, operador.organizacao_id, lead.id if lead else None, dados.marca
    )
    pesquisa = PesquisaMarca(
        organizacao_id=operador.organizacao_id,
        lead_id=lead.id if lead else None,
        empresa_id=empresa.id if empresa else (lead.empresa_id if lead else None),
        marca=dados.marca,
        atividade=dados.atividade,
        tipo_pesquisa="completa",
        classe_nice=None,
        duplicada=original is not None,
        pesquisa_original_id=original,
    )
    session.add(pesquisa)
    await session.commit()
    await session.refresh(pesquisa)
    return PesquisaMarcaCriada(
        id=pesquisa.id,
        relatorio_url=f"/admin/consulta/{pesquisa.id}",
        lead_id=lead.id if lead else None,
        duplicada=pesquisa.duplicada,
        pesquisa_original_id=pesquisa.pesquisa_original_id,
    )


@router.get("/{pesquisa_id}/contexto")
async def contexto_consulta(pesquisa_id: str, session: SessionDep, operador: OperadorDep) -> dict:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == operador.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Consulta nao encontrada")
    exclusao_status = (
        await session.execute(
            select(SolicitacaoExclusaoPesquisa.status)
            .where(
                SolicitacaoExclusaoPesquisa.pesquisa_id == pesquisa.id,
                SolicitacaoExclusaoPesquisa.status == "pendente",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "pesquisa_id": pesquisa.id,
        "lead_id": pesquisa.lead_id,
        "empresa_id": pesquisa.empresa_id,
        "duplicada": pesquisa.duplicada,
        "pesquisa_original_id": pesquisa.pesquisa_original_id,
        "exclusao_status": exclusao_status,
        "pode_excluir": operador.pode("leads.delete"),
    }


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
        raise HTTPException(status_code=404, detail="Consulta nao encontrada")
    return await gerar_resumo_pesquisa(session, pesquisa)

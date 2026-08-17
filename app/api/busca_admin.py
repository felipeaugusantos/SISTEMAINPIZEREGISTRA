from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import EventoAuditoria, ModeloRankingBusca
from app.search_model import gate_publicacao_busca, validar_transicao_status

router = APIRouter(prefix="/v1/admin/busca", tags=["busca-avançada"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]


class ModeloBuscaInput(BaseModel):
    versao: str = Field(min_length=1, max_length=60)
    algoritmo: str = Field(min_length=1, max_length=80)
    parametros: dict = Field(default_factory=dict)
    metricas: dict = Field(default_factory=dict)
    evidencias: dict = Field(default_factory=dict)
    dataset_version: str | None = None


class PublicacaoBuscaInput(BaseModel):
    status: Literal["SHADOW", "VALIDATION", "ACTIVE", "DISABLED"]
    baseline: dict | None = None
    revisoes_humanas: int = Field(default=0, ge=0)


@router.get("/modelos")
async def listar_modelos_busca(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (await session.execute(select(ModeloRankingBusca).where(ModeloRankingBusca.organizacao_id == usuario.organizacao_id).order_by(ModeloRankingBusca.criado_em.desc()))).scalars().all()
    return {"modelos": [{"id": item.id, "versao": item.versao, "algoritmo": item.algoritmo, "status": item.status, "metricas": item.metricas, "dataset_version": item.dataset_version, "bloqueado_motivo": item.bloqueado_motivo} for item in itens]}


@router.post("/modelos", status_code=201)
async def criar_modelo_busca(dados: ModeloBuscaInput, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    item = ModeloRankingBusca(organizacao_id=usuario.organizacao_id, versao=dados.versao, algoritmo=dados.algoritmo, parametros=dados.parametros, metricas=dados.metricas, evidencias=dados.evidencias, dataset_version=dados.dataset_version, status="SHADOW")
    session.add(item)
    session.add(EventoAuditoria(organizacao_id=usuario.organizacao_id, actor_id=usuario.id, ator=usuario.ator, acao="modelo_busca", recurso=f"modelo:{dados.versao}", sucesso=True, status_http=201, ip_hash=hash_ip(request.client.host if request.client else None), detalhes={"status": "SHADOW"}))
    await session.commit()
    return {"id": item.id, "versao": item.versao, "status": item.status}


@router.patch("/modelos/{modelo_id}/status")
async def publicar_modelo_busca(modelo_id: int, dados: PublicacaoBuscaInput, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    item = (await session.execute(select(ModeloRankingBusca).where(ModeloRankingBusca.id == modelo_id, ModeloRankingBusca.organizacao_id == usuario.organizacao_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Modelo de busca não encontrado")
    try:
        validar_transicao_status(item.status, dados.status)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    gate = gate_publicacao_busca(item.metricas or {}, dados.baseline, revisoes_humanas=dados.revisoes_humanas) if dados.status == "ACTIVE" else {"bloqueado": False, "regressoes": []}
    if gate["bloqueado"]:
        item.bloqueado_motivo = "; ".join(gate["regressoes"])
        item.status = "DISABLED"
        await session.commit()
        raise HTTPException(status_code=409, detail={"mensagem": "Publicação bloqueada pelo gate de busca", "regressoes": gate["regressoes"]})
    item.status = dados.status
    item.bloqueado_motivo = None
    if dados.status == "ACTIVE":
        item.publicado_em = datetime.now(UTC)
        item.publicado_por = usuario.ator
    session.add(EventoAuditoria(organizacao_id=usuario.organizacao_id, actor_id=usuario.id, ator=usuario.ator, acao="publicar_modelo", recurso=f"modelo:{item.id}", sucesso=True, status_http=200, ip_hash=hash_ip(request.client.host if request.client else None), detalhes={"status": dados.status, "gate": gate}))
    await session.commit()
    return {"id": item.id, "status": item.status, "gate": gate}

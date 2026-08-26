import re
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import EventoAuditoria, ModeloRankingBusca, ProjetoBuscaMarca
from app.search import buscar_marcas
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


class ConsultaAvancadaInput(BaseModel):
    marca: str = Field(min_length=1, max_length=200)
    tipo_pesquisa: str = "completa"
    estrategia: Literal["exata", "radical", "prefixo", "sufixo", "fonetica", "similaridade", "completa"] = "completa"
    classe_nice: str | None = Field(default=None, max_length=10)
    titular: str | None = Field(default=None, max_length=200)
    situacao: str | None = Field(default=None, max_length=30)
    data_inicio: date | None = None
    data_fim: date | None = None
    apresentacao: Literal["mista", "figurativa", "nominativa"] | None = None
    codigos_viena: list[str] = Field(default_factory=list, max_length=20)
    limite: int = Field(default=100, ge=1, le=500)


class ProjetoBuscaInput(ConsultaAvancadaInput):
    nome: str = Field(min_length=1, max_length=160)


def _slug_projeto(nome: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")
    return slug[:160] or "projeto-busca"


def _resultado_json(itens: list) -> list[dict]:
    return [
        {
            "processo": item.processo.numero,
            "titulo": item.processo.titulo,
            "apresentacao": item.processo.apresentacao,
            "situacao": item.processo.situacao,
            "data_deposito": item.processo.data_deposito,
            "criterios": item.criterios,
            "score": item.score.total,
            "fatores": item.score.fatores_json(),
            "evidencia": {
                "url_detalhe": f"/processos/{item.processo.numero}",
                "titulares": [titular.nome for titular in item.processo.titulares],
                "classes_nice": [c.codigo for c in item.processo.classificacoes if c.sistema == "nice"],
                "codigos_viena": [c.codigo for c in item.processo.classificacoes if c.sistema in {"viena", "vienna"}],
            },
        }
        for item in itens
    ]


@router.post("/consultar", summary="Busca avançada de anterioridade")
async def consultar_busca_avancada(dados: ConsultaAvancadaInput, session: SessionDep, usuario: ViewDep) -> dict:
    total, itens, evidencias = await buscar_marcas(
        session,
        marca=dados.marca,
        tipo_pesquisa=dados.tipo_pesquisa,
        classe_nice=dados.classe_nice,
        limite=dados.limite,
        estrategia=dados.estrategia,
        titular=dados.titular,
        situacao=dados.situacao,
        data_inicio=dados.data_inicio,
        data_fim=dados.data_fim,
        apresentacao=dados.apresentacao,
        codigos_viena=dados.codigos_viena,
    )
    return {
        "total": total,
        "resultados": _resultado_json(itens),
        "evidencias": evidencias,
        "revisao_humana_obrigatoria": True,
        "parecer_juridico_definitivo": False,
    }


@router.get("/projetos")
async def listar_projetos_busca(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (
            await session.execute(
                select(ProjetoBuscaMarca)
                .where(
                    ProjetoBuscaMarca.organizacao_id == usuario.organizacao_id,
                    ProjetoBuscaMarca.status == "ativo",
                )
                .order_by(ProjetoBuscaMarca.atualizado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "projetos": [
            {
                "id": item.id,
                "nome": item.nome,
                "slug": item.slug,
                "consulta": item.consulta,
                "executado_em": item.executado_em,
                "total": (item.ultima_execucao or {}).get("total", 0),
            }
            for item in itens
        ]
    }


@router.post("/projetos", status_code=201)
async def criar_projeto_busca(
    dados: ProjetoBuscaInput, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    projeto = ProjetoBuscaMarca(
        organizacao_id=usuario.organizacao_id,
        nome=dados.nome,
        slug=_slug_projeto(dados.nome),
        consulta=dados.model_dump(exclude={"nome"}),
        criado_por=usuario.ator,
    )
    session.add(projeto)
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Já existe um projeto com esse nome") from exc
    await session.refresh(projeto)
    return {"id": projeto.id, "nome": projeto.nome, "slug": projeto.slug}


@router.post("/projetos/{projeto_id}/reprocessar")
async def reprocessar_projeto_busca(projeto_id: int, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    projeto = (
        await session.execute(
            select(ProjetoBuscaMarca).where(
                ProjetoBuscaMarca.id == projeto_id,
                ProjetoBuscaMarca.organizacao_id == usuario.organizacao_id,
                ProjetoBuscaMarca.status == "ativo",
            )
        )
    ).scalar_one_or_none()
    if projeto is None:
        raise HTTPException(status_code=404, detail="Projeto de busca não encontrado")
    consulta = ConsultaAvancadaInput.model_validate(projeto.consulta)
    total, itens, evidencias = await buscar_marcas(session, **consulta.model_dump())
    projeto.ultima_execucao = {
        "total": total,
        "resultados": _resultado_json(itens),
        "evidencias": evidencias,
    }
    projeto.executado_em = datetime.now(UTC)
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao="reprocessar_projeto_busca",
            recurso=f"projeto:{projeto.id}",
            sucesso=True,
            status_http=200,
            detalhes={"total": total},
        )
    )
    await session.commit()
    return {
        "id": projeto.id,
        "executado_em": projeto.executado_em,
        "total": total,
        "resultados": _resultado_json(itens),
        "evidencias": evidencias,
        "revisao_humana_obrigatoria": True,
    }


@router.get("/modelos")
async def listar_modelos_busca(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (
            await session.execute(
                select(ModeloRankingBusca)
                .where(ModeloRankingBusca.organizacao_id == usuario.organizacao_id)
                .order_by(ModeloRankingBusca.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "modelos": [
            {
                "id": item.id,
                "versao": item.versao,
                "algoritmo": item.algoritmo,
                "status": item.status,
                "metricas": item.metricas,
                "dataset_version": item.dataset_version,
                "bloqueado_motivo": item.bloqueado_motivo,
            }
            for item in itens
        ]
    }


@router.post("/modelos", status_code=201)
async def criar_modelo_busca(
    dados: ModeloBuscaInput, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    item = ModeloRankingBusca(
        organizacao_id=usuario.organizacao_id,
        versao=dados.versao,
        algoritmo=dados.algoritmo,
        parametros=dados.parametros,
        metricas=dados.metricas,
        evidencias=dados.evidencias,
        dataset_version=dados.dataset_version,
        status="SHADOW",
    )
    session.add(item)
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao="modelo_busca",
            recurso=f"modelo:{dados.versao}",
            sucesso=True,
            status_http=201,
            ip_hash=hash_ip(request.client.host if request.client else None),
            detalhes={"status": "SHADOW"},
        )
    )
    await session.commit()
    return {"id": item.id, "versao": item.versao, "status": item.status}


@router.patch("/modelos/{modelo_id}/status")
async def publicar_modelo_busca(
    modelo_id: int,
    dados: PublicacaoBuscaInput,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    item = (
        await session.execute(
            select(ModeloRankingBusca).where(
                ModeloRankingBusca.id == modelo_id,
                ModeloRankingBusca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Modelo de busca não encontrado")
    try:
        validar_transicao_status(item.status, dados.status)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    gate = (
        gate_publicacao_busca(item.metricas or {}, dados.baseline, revisoes_humanas=dados.revisoes_humanas)
        if dados.status == "ACTIVE"
        else {"bloqueado": False, "regressoes": []}
    )
    if gate["bloqueado"]:
        item.bloqueado_motivo = "; ".join(gate["regressoes"])
        item.status = "DISABLED"
        await session.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "mensagem": "Publicação bloqueada pelo gate de busca",
                "regressoes": gate["regressoes"],
            },
        )
    item.status = dados.status
    item.bloqueado_motivo = None
    if dados.status == "ACTIVE":
        item.publicado_em = datetime.now(UTC)
        item.publicado_por = usuario.ator
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao="publicar_modelo",
            recurso=f"modelo:{item.id}",
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(request.client.host if request.client else None),
            detalhes={"status": dados.status, "gate": gate},
        )
    )
    await session.commit()
    return {"id": item.id, "status": item.status, "gate": gate}

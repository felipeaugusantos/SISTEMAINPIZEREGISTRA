from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import EventoAuditoria
from app.proxy import cliente_ip
from app.trademarks.benchmark import avaliar_benchmark
from app.trademarks.viena import buscar_anterioridades_viena

router = APIRouter(prefix="/v1/admin/figurativa", tags=["busca figurativa"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
OperadorDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]


class BenchmarkEntrada(BaseModel):
    casos: list[dict] = Field(min_length=1, max_length=500)


class ValidacaoHumanaEntrada(BaseModel):
    processo: str = Field(min_length=1, max_length=40)
    decisao: str = Field(pattern="^(confirmado|descartado|revisar)$")
    observacao: str = Field(min_length=3, max_length=2000)


@router.post("/benchmark")
async def benchmark_figurativo(
    dados: BenchmarkEntrada,
    request: Request,
    session: SessionDep,
    operador: OperadorDep,
) -> dict:
    metricas = avaliar_benchmark(dados.casos)
    session.add(EventoAuditoria(organizacao_id=operador.organizacao_id, actor_id=operador.id, ator=operador.ator, acao="benchmark", recurso="busca_figurativa", sucesso=True, status_http=200, ip_hash=hash_ip(cliente_ip(request)), detalhes=metricas))
    await session.commit()
    return metricas


@router.post("/validacoes-humanas")
async def validar_resultado_figurativo(
    dados: ValidacaoHumanaEntrada,
    request: Request,
    session: SessionDep,
    operador: OperadorDep,
) -> dict:
    session.add(EventoAuditoria(organizacao_id=operador.organizacao_id, actor_id=operador.id, ator=operador.ator, acao="validar", recurso=f"processo:{dados.processo}", resource_type="busca_figurativa", resource_id=dados.processo, sucesso=True, status_http=200, ip_hash=hash_ip(cliente_ip(request)), detalhes=dados.model_dump()))
    await session.commit()
    return {"registrado": True, **dados.model_dump()}


@router.get("/anterioridades")
async def anterioridades_figurativas(
    session: SessionDep,
    operador: OperadorDep,
    codigos: Annotated[str, Query(min_length=1, max_length=500)],
    apresentacao: Annotated[str | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    """Anterioridades figurativas por Classificação de Viena (códigos separados por vírgula)."""
    lista = [item.strip() for item in codigos.replace(";", ",").split(",") if item.strip()]
    if apresentacao and apresentacao not in {"mista", "figurativa"}:
        raise HTTPException(status_code=422, detail="Tipo de apresentação inválido.")
    resultados = await buscar_anterioridades_viena(session, lista, limite, apresentacao)
    return {
        "codigos": lista,
        "apresentacao": apresentacao,
        "total": len(resultados),
        "anterioridades": resultados,
    }

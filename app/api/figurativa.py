from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import EventoAuditoria, Processo
from app.proxy import cliente_ip
from app.trademarks.benchmark import avaliar_benchmark, avaliar_gate_regressao
from app.trademarks.viena import buscar_anterioridades_viena, contar_anterioridades_viena

router = APIRouter(prefix="/v1/admin/figurativa", tags=["busca figurativa"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
OperadorDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
# Achado médio da Fase 11 (auditoria da busca figurativa, 22/09/2026): as
# duas rotas abaixo registram uma decisão técnica/legal (confirmar ou
# descartar uma anterioridade figurativa) ou decidem se um novo modelo de
# similaridade pode ser publicado -- exigiam só leads.view, a mesma
# permissão ampla que qualquer perfil comercial tem. Os módulos irmãos que
# fazem o mesmo tipo de julgamento (app/api/fase2.py = Validação,
# app/api/fase3.py = Risco) exigem validation.review/risk.review pra
# escrever; aqui alinhado ao mesmo padrão (benchmark é decisão de ciclo de
# vida de modelo, mais perto de learning.manage).
ValidacaoDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("validation.review"))]
AprendizadoDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("learning.manage"))]


class BenchmarkEntrada(BaseModel):
    casos: list[dict] = Field(min_length=1, max_length=500)
    baseline: dict[str, float] | None = None
    tolerancia: float = Field(default=0.0, ge=0, le=0.1)


class ValidacaoHumanaEntrada(BaseModel):
    processo: str = Field(min_length=1, max_length=40)
    decisao: str = Field(pattern="^(confirmado|descartado|revisar)$")
    observacao: str = Field(min_length=3, max_length=2000)


@router.post("/benchmark")
async def benchmark_figurativo(
    dados: BenchmarkEntrada,
    request: Request,
    session: SessionDep,
    operador: AprendizadoDep,
) -> dict:
    # Achado baixo da Fase 11 (22/09/2026): casos com "relevantes"/
    # "retornados" em formato inesperado (ex.: itens não-hasheáveis) faziam
    # avaliar_benchmark estourar TypeError não tratado -- 500 cru em vez de
    # uma mensagem clara de validação.
    try:
        metricas = avaliar_benchmark(dados.casos)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Casos de benchmark com formato inválido -- 'relevantes' e 'retornados' devem ser listas de identificadores.",
        ) from exc
    gate = avaliar_gate_regressao(metricas, dados.baseline, dados.tolerancia)
    session.add(
        EventoAuditoria(
            organizacao_id=operador.organizacao_id,
            actor_id=operador.id,
            ator=operador.ator,
            acao="benchmark",
            recurso="busca_figurativa",
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes=metricas,
        )
    )
    await session.commit()
    return {**metricas, "gate_regressao": gate, "publicacao_permitida": not gate["bloqueado"]}


@router.post("/validacoes-humanas")
async def validar_resultado_figurativo(
    dados: ValidacaoHumanaEntrada,
    request: Request,
    session: SessionDep,
    operador: ValidacaoDep,
) -> dict:
    # Achado da Fase 14.5 (auditoria fina da busca figurativa, 23/09/2026):
    # aceitava qualquer string de 1-40 caracteres como "processo" sem checar
    # se corresponde a um Processo real -- uma decisão jurídica (confirmar
    # ou descartar uma anterioridade) podia ficar associada a um número de
    # processo inexistente ou digitado errado, sem nenhuma validação
    # server-side. Processo não tem organizacao_id (base pública replicada
    # da RPI, compartilhada entre tenants -- mesmo padrão de
    # buscar_anterioridades_viena), então a checagem não filtra por tenant.
    existe = (await session.execute(select(Processo.id).where(Processo.numero == dados.processo))).scalar_one_or_none()
    if existe is None:
        raise HTTPException(status_code=404, detail="Processo não encontrado.")
    session.add(
        EventoAuditoria(
            organizacao_id=operador.organizacao_id,
            actor_id=operador.id,
            ator=operador.ator,
            acao="validar",
            recurso=f"processo:{dados.processo}",
            resource_type="busca_figurativa",
            resource_id=dados.processo,
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes=dados.model_dump(),
        )
    )
    await session.commit()
    return {"registrado": True, **dados.model_dump()}


@router.get("/anterioridades")
async def anterioridades_figurativas(
    request: Request,
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
    # Achado da Fase 14.4 (auditoria fina, 23/09/2026): "total" era
    # len(resultados) DEPOIS do LIMIT aplicado -- o operador podia achar
    # que "50 resultados" era o total real quando existiam muito mais
    # anterioridades na base. Conta de verdade, sem o LIMIT.
    total = await contar_anterioridades_viena(session, lista, apresentacao)
    # Achado baixo da Fase 14.1 (auditoria fina da busca figurativa,
    # 23/09/2026): esta é a rota que qualquer operador comercial usa a toda
    # hora (busca real por Viena), mas só as rotas administrativas raramente
    # usadas (/benchmark, /validacoes-humanas) registravam EventoAuditoria --
    # nenhum rastro de quem pesquisou o quê.
    session.add(
        EventoAuditoria(
            organizacao_id=operador.organizacao_id,
            actor_id=operador.id,
            ator=operador.ator,
            acao="buscar",
            recurso="busca_figurativa",
            resource_type="anterioridades_viena",
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes={"codigos": lista, "apresentacao": apresentacao, "total": total, "retornados": len(resultados)},
        )
    )
    await session.commit()
    return {
        "codigos": lista,
        "apresentacao": apresentacao,
        "total": total,
        "retornados": len(resultados),
        "anterioridades": resultados,
    }

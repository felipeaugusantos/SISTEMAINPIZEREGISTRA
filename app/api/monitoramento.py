"""Monitoramento gerencial de erros e dificuldade de uso.

Pedido do dono (16/11/2026): um dashboard de acompanhamento de erro e
dificuldade de uso para o sistema central, no formato do que ele já conhece
do outro produto -- resumo do período, série por dia, uso por organização e
top erros -- em cima das tabelas de observabilidade que já existem
(EventoOperacional e EventoAuditoria). Nenhuma tabela ou migração nova:
todos os números saem de consultas sobre o que o middleware
(app/observability.py) e as auditorias de negócio já gravam.

"Dificuldade de uso" não tem captura dedicada no produto (não existem
eventos de abandono de tela ou funil). Os sinais expostos aqui são proxies
honestos sobre o que já é registrado:

- respostas 4xx por operação (tentativa frustrada: dado recusado, rota
  errada, permissão negada);
- ações com falha repetidas pelo mesmo usuário no mesmo recurso
  (travamento: ele tenta de novo e continua sem conseguir);
- usuários com mais falhas acumuladas no período;
- operações mais lentas (p95), que empurram o usuário a desistir.

Mesmo recorte de acesso da Observabilidade: permissão production.view e
perfil administrador/tech (ou superadmin), porque os números cruzam
organizações.
"""

from datetime import UTC, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.observabilidade import _exigir_acesso_tech
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import EventoAuditoria, EventoOperacional, Organizacao

router = APIRouter(prefix="/v1/admin/monitoramento", tags=["monitoramento"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
TechDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("production.view"))]

# Datas de negócio seguem America/Sao_Paulo (decisão registrada do projeto):
# os buckets "por dia" e a janela do período usam esse fuso, não UTC.
FUSO_NEGOCIO = ZoneInfo("America/Sao_Paulo")


def _janela(dias: int) -> tuple[datetime, list[datetime]]:
    """Início da janela (aware) e a lista completa de dias do período, no
    fuso de negócio -- a série volta com dia zerado quando não há evento."""
    hoje = datetime.now(UTC).astimezone(FUSO_NEGOCIO).date()
    primeiro = hoje - timedelta(days=dias - 1)
    desde = datetime.combine(primeiro, time.min, tzinfo=FUSO_NEGOCIO)
    dias_lista = [primeiro + timedelta(deslocamento) for deslocamento in range(dias)]
    return desde, dias_lista


def _dia(coluna):  # noqa: ANN001, ANN202 -- expressão SQLAlchemy
    return func.date(func.timezone("America/Sao_Paulo", coluna))


@router.get("/resumo")
async def resumo_monitoramento(
    session: SessionDep,
    usuario: TechDep,
    dias: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict:
    """Cartões do topo: ações, usuários e organizações ativas, organizações
    ativas sem nenhum uso no período, erros e tentativas frustradas."""
    _exigir_acesso_tech(usuario)
    desde, _ = _janela(dias)

    acoes, usuarios_ativos, organizacoes_ativas, acoes_falhas = (
        await session.execute(
            select(
                func.count(),
                func.count(func.distinct(EventoAuditoria.actor_id)),
                func.count(func.distinct(EventoAuditoria.organizacao_id)),
                func.sum(case((EventoAuditoria.sucesso.is_(False), 1), else_=0)),
            ).where(EventoAuditoria.criado_em >= desde)
        )
    ).one()

    requisicoes, erros = (
        await session.execute(
            select(
                func.count(),
                func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0)),
            ).where(EventoOperacional.criado_em >= desde)
        )
    ).one()

    com_uso = (
        select(EventoAuditoria.organizacao_id)
        .where(EventoAuditoria.criado_em >= desde, EventoAuditoria.organizacao_id.is_not(None))
        .distinct()
        .scalar_subquery()
    )
    organizacoes_sem_uso, total_organizacoes_ativas = (
        await session.execute(
            select(
                func.sum(case((Organizacao.id.not_in(com_uso), 1), else_=0)),
                func.count(),
            ).where(Organizacao.status == "ativa")
        )
    ).one()

    acoes = int(acoes or 0)
    acoes_falhas = int(acoes_falhas or 0)
    requisicoes = int(requisicoes or 0)
    erros = int(erros or 0)
    return {
        "gerado_em": datetime.now(UTC),
        "dias": dias,
        "acoes": acoes,
        "usuarios_ativos": int(usuarios_ativos or 0),
        "organizacoes_ativas": int(organizacoes_ativas or 0),
        "total_organizacoes_ativas": int(total_organizacoes_ativas or 0),
        "organizacoes_sem_uso": int(organizacoes_sem_uso or 0),
        "acoes_com_falha": acoes_falhas,
        "taxa_falha_acoes": acoes_falhas / acoes if acoes else 0,
        "requisicoes": requisicoes,
        "erros": erros,
        "taxa_erros": erros / requisicoes if requisicoes else 0,
    }


@router.get("/serie")
async def serie_monitoramento(
    session: SessionDep,
    usuario: TechDep,
    dias: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict:
    """Ações, tentativas frustradas e erros por dia (fuso de negócio), com
    dias sem movimento zerados para o gráfico ficar contínuo."""
    _exigir_acesso_tech(usuario)
    desde, dias_lista = _janela(dias)

    dia_auditoria = _dia(EventoAuditoria.criado_em)
    linhas_auditoria = (
        await session.execute(
            select(
                dia_auditoria,
                func.count(),
                func.sum(case((EventoAuditoria.sucesso.is_(False), 1), else_=0)),
            )
            .where(EventoAuditoria.criado_em >= desde)
            .group_by(dia_auditoria)
            .order_by(dia_auditoria)
        )
    ).all()
    por_dia_auditoria = {dia: (int(acoes or 0), int(falhas or 0)) for dia, acoes, falhas in linhas_auditoria}

    dia_operacional = _dia(EventoOperacional.criado_em)
    linhas_operacional = (
        await session.execute(
            select(
                dia_operacional,
                func.count(),
                func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0)),
            )
            .where(EventoOperacional.criado_em >= desde)
            .group_by(dia_operacional)
            .order_by(dia_operacional)
        )
    ).all()
    por_dia_operacional = {dia: (int(requisicoes or 0), int(erros or 0)) for dia, requisicoes, erros in linhas_operacional}

    pontos = []
    for dia in dias_lista:
        acoes, frustradas = por_dia_auditoria.get(dia, (0, 0))
        requisicoes, erros = por_dia_operacional.get(dia, (0, 0))
        pontos.append(
            {
                "dia": dia.isoformat(),
                "acoes": acoes,
                "frustradas": frustradas,
                "requisicoes": requisicoes,
                "erros": erros,
            }
        )
    return {"gerado_em": datetime.now(UTC), "dias": dias, "pontos": pontos}


@router.get("/uso-organizacoes")
async def uso_por_organizacao(
    session: SessionDep,
    usuario: TechDep,
    dias: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict:
    """Uso por organização (ações, usuários ativos, falhas e última
    atividade) e a lista das organizações ativas sem nenhum uso no período
    -- o sinal de churn silencioso."""
    _exigir_acesso_tech(usuario)
    desde, _ = _janela(dias)

    linhas = (
        await session.execute(
            select(
                Organizacao.id,
                Organizacao.nome,
                func.count(),
                func.count(func.distinct(EventoAuditoria.actor_id)),
                func.sum(case((EventoAuditoria.sucesso.is_(False), 1), else_=0)),
                func.max(EventoAuditoria.criado_em),
            )
            .join(Organizacao, Organizacao.id == EventoAuditoria.organizacao_id)
            .where(EventoAuditoria.criado_em >= desde)
            .group_by(Organizacao.id, Organizacao.nome)
            .order_by(func.count().desc())
            .limit(100)
        )
    ).all()

    com_uso = (
        select(EventoAuditoria.organizacao_id)
        .where(EventoAuditoria.criado_em >= desde, EventoAuditoria.organizacao_id.is_not(None))
        .distinct()
        .scalar_subquery()
    )
    sem_uso = (
        await session.execute(
            select(Organizacao.id, Organizacao.nome)
            .where(Organizacao.status == "ativa", Organizacao.id.not_in(com_uso))
            .order_by(Organizacao.nome)
            .limit(100)
        )
    ).all()

    return {
        "gerado_em": datetime.now(UTC),
        "dias": dias,
        "organizacoes": [
            {
                "id": org_id,
                "nome": nome,
                "acoes": int(acoes or 0),
                "usuarios_ativos": int(usuarios or 0),
                "falhas": int(falhas or 0),
                "taxa_falha": (int(falhas or 0) / int(acoes)) if acoes else 0,
                "ultima_atividade": ultima,
            }
            for org_id, nome, acoes, usuarios, falhas, ultima in linhas
        ],
        "sem_uso": [{"id": org_id, "nome": nome} for org_id, nome in sem_uso],
    }


@router.get("/top-erros")
async def top_erros(
    session: SessionDep,
    usuario: TechDep,
    dias: Annotated[int, Query(ge=1, le=90)] = 30,
    limite: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Erros agrupados por código e operação, mais frequentes primeiro --
    o "o que está quebrando mais" do período."""
    _exigir_acesso_tech(usuario)
    desde, _ = _janela(dias)
    quantidade = func.count()
    linhas = (
        await session.execute(
            select(
                EventoOperacional.codigo_erro,
                EventoOperacional.operacao,
                EventoOperacional.status_http,
                quantidade,
                func.max(EventoOperacional.criado_em),
                func.avg(EventoOperacional.duracao_ms),
            )
            .where(EventoOperacional.criado_em >= desde, EventoOperacional.sucesso.is_(False))
            .group_by(EventoOperacional.codigo_erro, EventoOperacional.operacao, EventoOperacional.status_http)
            .order_by(quantidade.desc())
            .limit(limite)
        )
    ).all()
    return {
        "gerado_em": datetime.now(UTC),
        "dias": dias,
        "erros": [
            {
                "codigo_erro": codigo,
                "operacao": operacao,
                "status_http": status,
                "quantidade": int(total or 0),
                "ultima_ocorrencia": ultima,
                "duracao_media_ms": round(float(media or 0), 1),
            }
            for codigo, operacao, status, total, ultima, media in linhas
        ],
    }


@router.get("/dificuldade")
async def dificuldade_de_uso(
    session: SessionDep,
    usuario: TechDep,
    dias: Annotated[int, Query(ge=1, le=90)] = 30,
    limite: Annotated[int, Query(ge=1, le=100)] = 15,
) -> dict:
    """Proxies de dificuldade de uso (o produto não captura eventos de
    abandono de tela): tentativas frustradas (4xx), usuários com mais
    falhas, recursos em que o mesmo usuário falha 3+ vezes e lentidão."""
    _exigir_acesso_tech(usuario)
    desde, _ = _janela(dias)
    quantidade = func.count()

    validacoes = (
        await session.execute(
            select(
                EventoOperacional.operacao,
                EventoOperacional.status_http,
                quantidade,
                func.max(EventoOperacional.criado_em),
            )
            .where(
                EventoOperacional.criado_em >= desde,
                EventoOperacional.status_http >= 400,
                EventoOperacional.status_http < 500,
            )
            .group_by(EventoOperacional.operacao, EventoOperacional.status_http)
            .order_by(quantidade.desc())
            .limit(limite)
        )
    ).all()

    usuarios = (
        await session.execute(
            select(
                EventoAuditoria.ator,
                Organizacao.nome,
                quantidade,
                func.count(func.distinct(EventoAuditoria.recurso)),
                func.max(EventoAuditoria.criado_em),
            )
            .outerjoin(Organizacao, Organizacao.id == EventoAuditoria.organizacao_id)
            .where(EventoAuditoria.criado_em >= desde, EventoAuditoria.sucesso.is_(False))
            .group_by(EventoAuditoria.ator, Organizacao.nome)
            .order_by(quantidade.desc())
            .limit(limite)
        )
    ).all()

    travados = (
        await session.execute(
            select(
                EventoAuditoria.ator,
                EventoAuditoria.recurso,
                quantidade,
                func.max(EventoAuditoria.criado_em),
            )
            .where(EventoAuditoria.criado_em >= desde, EventoAuditoria.sucesso.is_(False))
            .group_by(EventoAuditoria.ator, EventoAuditoria.recurso)
            .having(quantidade >= 3)
            .order_by(quantidade.desc())
            .limit(limite)
        )
    ).all()

    p95 = func.percentile_cont(0.95).within_group(EventoOperacional.duracao_ms)
    lentidao = (
        await session.execute(
            select(
                EventoOperacional.componente,
                EventoOperacional.operacao,
                quantidade,
                func.avg(EventoOperacional.duracao_ms),
                p95,
                func.max(EventoOperacional.duracao_ms),
            )
            .where(EventoOperacional.criado_em >= desde)
            .group_by(EventoOperacional.componente, EventoOperacional.operacao)
            .having(quantidade >= 5)
            .order_by(p95.desc())
            .limit(limite)
        )
    ).all()

    return {
        "gerado_em": datetime.now(UTC),
        "dias": dias,
        "tentativas_frustradas": [
            {
                "operacao": operacao,
                "status_http": status,
                "quantidade": int(total or 0),
                "ultima_ocorrencia": ultima,
            }
            for operacao, status, total, ultima in validacoes
        ],
        "usuarios_com_falhas": [
            {
                "ator": ator,
                "organizacao": organizacao,
                "falhas": int(total or 0),
                "recursos_distintos": int(recursos or 0),
                "ultima_ocorrencia": ultima,
            }
            for ator, organizacao, total, recursos, ultima in usuarios
        ],
        "recursos_travados": [
            {
                "ator": ator,
                "recurso": recurso,
                "falhas": int(total or 0),
                "ultima_ocorrencia": ultima,
            }
            for ator, recurso, total, ultima in travados
        ],
        "lentidao": [
            {
                "componente": componente,
                "operacao": operacao,
                "requisicoes": int(total or 0),
                "media_ms": round(float(media or 0), 1),
                "p95_ms": round(float(p or 0), 1),
                "max_ms": int(maximo or 0),
            }
            for componente, operacao, total, media, p, maximo in lentidao
        ],
    }

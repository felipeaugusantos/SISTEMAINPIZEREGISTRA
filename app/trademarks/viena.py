"""Busca figurativa por Classificação de Viena.

A busca nominativa (por nome) não cobre a dimensão figurativa das marcas mistas e
figurativas. Aqui usamos os códigos de Viena — já presentes na base — para achar
anterioridades por sobreposição/afinidade de elementos figurativos, no mesmo espírito
da afinidade de classes de Nice.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AfinidadeViena, ClassificacaoMarca, Processo, TipoProcesso


@dataclass(frozen=True, slots=True)
class ResultadoAfinidadeViena:
    nivel: str
    rotulo: str
    justificativa: str
    revisao: str
    codigos_comuns: tuple[str, ...]


def _categoria(codigo: str) -> str:
    return codigo.split(".", 1)[0]


def _divisao(codigo: str) -> str:
    partes = codigo.split(".")
    return ".".join(partes[:2]) if len(partes) >= 2 else codigo


def avaliar_afinidade_viena(
    codigos_alvo: list[str],
    codigos_processo: list[str],
    matriz: list[AfinidadeViena],
) -> ResultadoAfinidadeViena:
    alvo = tuple(dict.fromkeys(c for c in codigos_alvo if c))
    processo = tuple(dict.fromkeys(c for c in codigos_processo if c))
    if not alvo or not processo:
        return ResultadoAfinidadeViena(
            "sem_dados",
            "Dados insuficientes",
            "Não há códigos de Viena suficientes para comparar.",
            "pendente",
            (),
        )

    iguais = sorted(set(alvo) & set(processo))
    if iguais:
        return ResultadoAfinidadeViena(
            "identica",
            "Mesmos elementos figurativos",
            f"Código(s) de Viena coincidente(s): {', '.join(iguais)}.",
            "nao_aplicavel",
            tuple(iguais),
        )

    # Proximidade hierárquica dos códigos de Viena (categoria.divisão.seção).
    divisoes_alvo = {_divisao(c) for c in alvo}
    divisoes_comuns = sorted({_divisao(c) for c in processo if _divisao(c) in divisoes_alvo})
    if divisoes_comuns:
        return ResultadoAfinidadeViena(
            "alta",
            "Mesma divisão de Viena",
            f"Divisão figurativa em comum: {', '.join(divisoes_comuns)}.",
            "nao_aplicavel",
            tuple(divisoes_comuns),
        )
    categorias_alvo = {_categoria(c) for c in alvo}
    categorias_comuns = sorted({_categoria(c) for c in processo if _categoria(c) in categorias_alvo})
    if categorias_comuns:
        return ResultadoAfinidadeViena(
            "moderada",
            "Mesma categoria de Viena",
            f"Categoria figurativa em comum: {', '.join(categorias_comuns)}.",
            "nao_aplicavel",
            tuple(categorias_comuns),
        )

    # Relações curadas (não-hierárquicas), validadas por humano.
    por_par = {
        tuple(sorted((item.codigo_origem, item.codigo_destino))): item
        for item in matriz
        if item.status_revisao != "rejeitada"
    }
    encontrados = [
        por_par[tuple(sorted((origem, destino)))]
        for origem in alvo
        for destino in processo
        if tuple(sorted((origem, destino))) in por_par
    ]
    if encontrados:
        prioridade = {"alta": 0, "moderada": 1}
        melhor = sorted(encontrados, key=lambda item: prioridade.get(item.nivel.lower(), 9))[0]
        pendente = melhor.status_revisao != "aprovada"
        return ResultadoAfinidadeViena(
            melhor.nivel.lower(),
            "Afinidade figurativa mapeada",
            melhor.justificativa,
            "pendente" if pendente else "aprovada",
            (),
        )

    return ResultadoAfinidadeViena(
        "nao_mapeada",
        "Sem relação figurativa",
        "Nenhum elemento figurativo em comum foi identificado.",
        "pendente",
        (),
    )


async def buscar_anterioridades_viena(
    session: AsyncSession,
    codigos: list[str],
    limite: int = 50,
    apresentacao: str | None = None,
) -> list[dict]:
    """Marcas que compartilham códigos de Viena, ranqueadas por nº de códigos em comum."""
    alvo = [c.strip() for c in codigos if c.strip()]
    if not alvo:
        return []
    sobreposicao = func.count(func.distinct(ClassificacaoMarca.codigo)).label("sobreposicao")
    codigos_comuns = func.array_agg(func.distinct(ClassificacaoMarca.codigo)).label("codigos")
    stmt = (
        select(
            Processo.numero,
            Processo.titulo,
            Processo.apresentacao,
            Processo.data_deposito,
            codigos_comuns,
            sobreposicao,
        )
        .join(ClassificacaoMarca, ClassificacaoMarca.processo_id == Processo.id)
        .where(
            Processo.tipo == TipoProcesso.MARCA,
            ClassificacaoMarca.sistema == "vienna",
            ClassificacaoMarca.codigo.in_(alvo),
        )
    )
    if apresentacao:
        stmt = stmt.where(Processo.apresentacao == apresentacao)
    stmt = (
        stmt.group_by(Processo.id)
        .order_by(sobreposicao.desc(), Processo.data_deposito.desc().nullslast())
        .limit(limite)
    )
    linhas = (await session.execute(stmt)).all()
    return [
        {
            "numero": numero,
            "titulo": titulo,
            "apresentacao": apresentacao,
            "data_deposito": data_deposito,
            "codigos_viena": sorted(codigos or []),
            "codigos_em_comum": int(total),
            "url_detalhe": f"/processos/{numero}",
        }
        for numero, titulo, apresentacao, data_deposito, codigos, total in linhas
    ]

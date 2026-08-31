"""Roda o dataset de conflitos oficiais do INPI contra buscar_marcas() e mede
recall/precisão/MRR/latência.

Cada caso do dataset (data/search-benchmark-candidato-inpi.v1.json, gerado por
app.cli.gerar_candidatos_busca) é uma marca que o INPI já indeferiu citando uma
anterioridade específica -- serve para checar, antes e depois de uma mudança no
motor de busca/ranking/risco, se ela de fato melhora (ou não regride) a capacidade
de achar essas anterioridades reais no topo do resultado.
"""

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from time import perf_counter
from typing import Any

from app.database import session_factory
from app.search import buscar_marcas

CAMINHO_PADRAO = Path("data/search-benchmark-candidato-inpi.v1.json")
LIMITES_K = (5, 10, 20)


async def avaliar_caso(session: Any, caso: dict[str, Any], limite_resultados: int) -> dict[str, Any]:
    inicio = perf_counter()
    _total, ocorrencias, _evidencias = await buscar_marcas(
        session,
        marca=caso["marca"],
        tipo_pesquisa=caso.get("tipo_pesquisa", "dupla"),
        classe_nice=caso.get("classe_nice"),
        limite=limite_resultados,
    )
    duracao_ms = (perf_counter() - inicio) * 1000
    processos_encontrados = [item.processo.numero for item in ocorrencias]
    esperados = sorted(set(caso["processos_esperados"]))
    posicoes = {
        processo: processos_encontrados.index(processo) + 1
        for processo in esperados
        if processo in processos_encontrados
    }
    return {
        "id": caso["id"],
        "marca_pesquisada": caso["marca"],
        "processos_esperados": esperados,
        "processos_encontrados": processos_encontrados,
        "posicoes_esperadas": posicoes,
        "duracao_ms": round(duracao_ms, 2),
    }


def _recall_precision_em(resultados: list[dict[str, Any]], k: int) -> dict[str, float]:
    acertos_recall = 0
    somatorio_precision = 0.0
    for resultado in resultados:
        topo = set(resultado["processos_encontrados"][:k])
        esperados = set(resultado["processos_esperados"])
        if esperados & topo:
            acertos_recall += 1
        somatorio_precision += len(esperados & topo) / k
    total = len(resultados) or 1
    return {"recall": acertos_recall / total, "precision": somatorio_precision / total}


def _mrr(resultados: list[dict[str, Any]]) -> float:
    reciprocos = []
    for resultado in resultados:
        posicoes = resultado["posicoes_esperadas"].values()
        reciprocos.append(1 / min(posicoes) if posicoes else 0.0)
    return sum(reciprocos) / len(reciprocos) if reciprocos else 0.0


def _percentil(valores_ordenados: list[float], fracao: float) -> float:
    if not valores_ordenados:
        return 0.0
    indice = min(len(valores_ordenados) - 1, int(len(valores_ordenados) * fracao))
    return valores_ordenados[indice]


async def executar(args: argparse.Namespace) -> None:
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    casos = dataset["casos"][: args.limite] if args.limite else dataset["casos"]

    resultados: list[dict[str, Any]] = []
    async with session_factory() as session:
        for caso in casos:
            resultados.append(await avaliar_caso(session, caso, args.limite_resultados))

    latencias = sorted(resultado["duracao_ms"] for resultado in resultados)
    falsos_negativos_criticos = sum(
        1 for resultado in resultados if not (set(resultado["processos_esperados"]) & set(resultado["processos_encontrados"]))
    )

    saida = {
        "dataset_version": dataset["dataset_version"],
        "dataset_revisao": dataset.get("revisao"),
        "casos": resultados,
        "metricas": {str(k): _recall_precision_em(resultados, k) for k in LIMITES_K},
        "mrr": _mrr(resultados),
        "latencia_ms": {
            "media": round(statistics.fmean(latencias), 2) if latencias else 0.0,
            "p50": _percentil(latencias, 0.50),
            "p95": _percentil(latencias, 0.95),
            "p99": _percentil(latencias, 0.99),
        },
        "falsos_negativos_criticos": falsos_negativos_criticos,
        "limite_resultados": args.limite_resultados,
    }

    print(json.dumps({chave: valor for chave, valor in saida.items() if chave != "casos"}, ensure_ascii=False, indent=2))

    if args.saida:
        args.saida.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Resultado salvo em {args.saida}")

    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        recall20_atual = saida["metricas"]["20"]["recall"]
        recall20_baseline = baseline["metricas"]["20"]["recall"]
        if recall20_atual < recall20_baseline - args.tolerancia_recall:
            print(
                f"REGRESSÃO: recall@20 caiu de {recall20_baseline:.4f} para {recall20_atual:.4f} "
                f"(tolerância={args.tolerancia_recall:.4f})"
            )
            raise SystemExit(1)
        print(f"Sem regressão de recall@20 em relação ao baseline ({recall20_baseline:.4f} -> {recall20_atual:.4f}).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Roda o benchmark de conflitos oficiais do INPI contra buscar_marcas() "
        "e mede recall/precisão/MRR/latência."
    )
    parser.add_argument("--dataset", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--limite", type=int, default=None, help="Quantos casos do dataset rodar (default: todos).")
    parser.add_argument("--limite-resultados", type=int, default=20, help="Top-K retornado por buscar_marcas().")
    parser.add_argument("--saida", type=Path, default=None, help="Onde salvar o resultado completo em JSON.")
    parser.add_argument(
        "--baseline", type=Path, default=None, help="Resultado anterior para checar regressão de recall@20."
    )
    parser.add_argument(
        "--tolerancia-recall", type=float, default=0.02, help="Queda máxima aceita em recall@20 (fração absoluta)."
    )
    args = parser.parse_args()
    asyncio.run(executar(args))


if __name__ == "__main__":
    main()

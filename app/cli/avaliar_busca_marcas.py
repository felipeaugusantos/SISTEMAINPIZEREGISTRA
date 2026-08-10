import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

from app.database import session_factory
from app.search import buscar_marcas


def metricas_resultado(encontrados: list[str], esperados: set[str]) -> tuple[float, float]:
    if not esperados:
        return 0.0, 0.0
    acertos = len(set(encontrados) & esperados)
    recall = acertos / len(esperados)
    precision = acertos / len(encontrados) if encontrados else 0.0
    return recall, precision


async def avaliar(caminho: Path, limite: int) -> dict:
    casos = json.loads(caminho.read_text(encoding="utf-8"))
    if not casos:
        raise ValueError("O arquivo de benchmark nao possui casos")
    resultados = []
    async with session_factory() as session:
        for caso in casos:
            inicio = perf_counter()
            _, itens, evidencias = await buscar_marcas(
                session,
                caso["marca"],
                "dupla",
                caso.get("classe_nice"),
                limite=limite,
            )
            duracao_ms = round((perf_counter() - inicio) * 1000)
            encontrados = [processo.numero for processo, _ in itens]
            esperados = set(caso.get("processos_esperados", []))
            recall, precision = metricas_resultado(encontrados, esperados)
            resultados.append(
                {
                    "marca": caso["marca"],
                    "recall_k": recall,
                    "precision_k": precision,
                    "latencia_ms": duracao_ms,
                    "retornados": len(encontrados),
                    "esperados": len(esperados),
                    "versao": evidencias["versao_algoritmo"],
                }
            )
    return {
        "casos": resultados,
        "recall_medio": sum(item["recall_k"] for item in resultados) / len(resultados),
        "precision_media": sum(item["precision_k"] for item in resultados) / len(resultados),
        "latencia_media_ms": sum(item["latencia_ms"] for item in resultados) / len(resultados),
        "latencia_maxima_ms": max(item["latencia_ms"] for item in resultados),
        "k": limite,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Avalia a Busca V3 contra um conjunto revisado")
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--limite", type=int, default=50)
    parser.add_argument("--saida", type=Path)
    args = parser.parse_args()
    resultado = asyncio.run(avaliar(args.arquivo, args.limite))
    texto = json.dumps(resultado, ensure_ascii=False, indent=2)
    if args.saida:
        args.saida.write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()

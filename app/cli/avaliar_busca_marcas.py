import argparse
import asyncio
import json
import math
from pathlib import Path
from time import perf_counter
from typing import Any

from app.database import session_factory
from app.search import buscar_marcas

KS_PADRAO = (5, 10, 20)


def metricas_resultado(encontrados: list[str], esperados: set[str]) -> tuple[float, float]:
    """Compatibilidade com a métrica histórica, usando todos os resultados recebidos."""
    if not esperados:
        return 0.0, 0.0
    acertos = len(set(encontrados) & esperados)
    recall = acertos / len(esperados)
    precision = acertos / len(encontrados) if encontrados else 0.0
    return recall, precision


def metricas_em_k(
    encontrados: list[str], esperados: set[str], ks: tuple[int, ...] = KS_PADRAO
) -> dict[str, dict[str, float]]:
    if not esperados:
        raise ValueError("Cada caso precisa de pelo menos um processo esperado")
    metricas: dict[str, dict[str, float]] = {}
    for k in ks:
        topo = encontrados[:k]
        acertos = len(set(topo) & esperados)
        metricas[str(k)] = {
            "recall": acertos / len(esperados),
            "precision": acertos / k,
        }
    return metricas


def reciprocal_rank(encontrados: list[str], esperados: set[str]) -> float:
    for posicao, processo in enumerate(encontrados, start=1):
        if processo in esperados:
            return 1 / posicao
    return 0.0


def percentil(valores: list[float], percentual: float) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    indice = (len(ordenados) - 1) * percentual
    inferior = math.floor(indice)
    superior = math.ceil(indice)
    if inferior == superior:
        return ordenados[inferior]
    fracao = indice - inferior
    return ordenados[inferior] + (ordenados[superior] - ordenados[inferior]) * fracao


def carregar_dataset(caminho: Path, *, permitir_pendente: bool = False) -> dict[str, Any]:
    dataset = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(dataset, dict) or not isinstance(dataset.get("casos"), list):
        raise ValueError("Dataset deve ser um objeto versionado com a lista 'casos'")
    if not dataset.get("dataset_version"):
        raise ValueError("Dataset sem 'dataset_version'")
    revisao = dataset.get("revisao") or {}
    if revisao.get("status") != "aprovado" and not permitir_pendente:
        raise ValueError(
            "Dataset ainda não foi aprovado por especialista; use --permitir-pendente "
            "somente para gerar uma baseline candidata"
        )
    if revisao.get("status") == "aprovado" and not all(
        revisao.get(campo) for campo in ("revisado_por", "papel", "revisado_em")
    ):
        raise ValueError("Revisão aprovada precisa informar revisor, papel e data")
    if not dataset["casos"]:
        raise ValueError("O arquivo de benchmark não possui casos")
    ids: set[str] = set()
    for caso in dataset["casos"]:
        caso_id = str(caso.get("id") or "")
        esperados = caso.get("processos_esperados") or []
        if not caso_id or caso_id in ids:
            raise ValueError("Cada caso precisa de um id único")
        if not caso.get("marca") or not esperados:
            raise ValueError(f"Caso {caso_id} sem marca ou processos esperados")
        criticos = set(caso.get("processos_criticos") or [])
        if not criticos.issubset(set(esperados)):
            raise ValueError(f"Caso {caso_id}: processos críticos devem estar entre os esperados")
        ids.add(caso_id)
    return dataset


async def avaliar(
    caminho: Path,
    limite: int = max(KS_PADRAO),
    *,
    permitir_pendente: bool = False,
) -> dict[str, Any]:
    if limite < max(KS_PADRAO):
        raise ValueError(f"O limite deve ser no mínimo {max(KS_PADRAO)}")
    dataset = carregar_dataset(caminho, permitir_pendente=permitir_pendente)
    resultados: list[dict[str, Any]] = []
    async with session_factory() as session:
        for caso in dataset["casos"]:
            inicio = perf_counter()
            _, itens, evidencias = await buscar_marcas(
                session,
                caso["marca"],
                caso.get("tipo_pesquisa", "dupla"),
                caso.get("classe_nice"),
                limite=limite,
            )
            duracao_ms = round((perf_counter() - inicio) * 1000, 2)
            encontrados = [item.processo.numero for item in itens]
            esperados = set(caso["processos_esperados"])
            criticos = set(caso.get("processos_criticos") or [])
            posicoes = {
                processo: encontrados.index(processo) + 1 if processo in encontrados else None
                for processo in sorted(esperados)
            }
            falsos_negativos = sorted(esperados - set(encontrados[: max(KS_PADRAO)]))
            falsos_negativos_criticos = sorted(criticos & set(falsos_negativos))
            resultados.append(
                {
                    "id": caso["id"],
                    "marca_pesquisada": caso["marca"],
                    "processos_esperados": sorted(esperados),
                    "processos_encontrados": encontrados,
                    "posicoes_esperadas": posicoes,
                    "resultados": [
                        {
                            "posicao": posicao,
                            "processo": item.processo.numero,
                            "titulo": item.processo.titulo,
                            "score": item.score.total,
                            "motivo_score": item.score.fatores_json(),
                        }
                        for posicao, item in enumerate(itens, start=1)
                    ],
                    "metricas": metricas_em_k(encontrados, esperados),
                    "reciprocal_rank": reciprocal_rank(encontrados, esperados),
                    "latencia_ms": duracao_ms,
                    "falsos_negativos": falsos_negativos,
                    "falsos_negativos_criticos": falsos_negativos_criticos,
                    "versao_busca": evidencias["versao_algoritmo"],
                    "versao_ranking": evidencias["ranking"]["versao"],
                }
            )

    latencias = [item["latencia_ms"] for item in resultados]
    agregado_por_k = {
        str(k): {
            "recall": sum(item["metricas"][str(k)]["recall"] for item in resultados)
            / len(resultados),
            "precision": sum(item["metricas"][str(k)]["precision"] for item in resultados)
            / len(resultados),
        }
        for k in KS_PADRAO
    }
    return {
        "dataset_version": dataset["dataset_version"],
        "dataset_revisao": dataset["revisao"],
        "casos": resultados,
        "metricas": agregado_por_k,
        "mrr": sum(item["reciprocal_rank"] for item in resultados) / len(resultados),
        "latencia_ms": {
            "media": sum(latencias) / len(latencias),
            "p50": percentil(latencias, 0.50),
            "p95": percentil(latencias, 0.95),
            "p99": percentil(latencias, 0.99),
        },
        "falsos_negativos_criticos": sum(
            len(item["falsos_negativos_criticos"]) for item in resultados
        ),
        "limite_resultados": limite,
    }


def avaliar_regressao(
    atual: dict[str, Any], baseline: dict[str, Any], limites: dict[str, Any]
) -> list[str]:
    falhas: list[str] = []
    tolerancias = limites["regressao_maxima"]
    criticos_atuais = int(atual.get("falsos_negativos_criticos", 0))
    criticos_baseline = int(baseline.get("falsos_negativos_criticos", 0))
    if criticos_atuais > int(limites.get("falsos_negativos_criticos_maximos", 0)):
        falhas.append(f"falsos negativos críticos: {criticos_atuais}")
    if criticos_atuais > criticos_baseline:
        falhas.append(
            f"novos falsos negativos críticos: baseline={criticos_baseline}, atual={criticos_atuais}"
        )
    for k in KS_PADRAO:
        chave = str(k)
        for metrica in ("recall", "precision"):
            anterior = float(baseline["metricas"][chave][metrica])
            corrente = float(atual["metricas"][chave][metrica])
            # Recall jurídico não admite regressão: a tolerância configurável só
            # pode ser aplicada à precisão.
            tolerancia = 0.0 if metrica == "recall" else float(tolerancias[f"{metrica}_at_{k}"])
            if corrente < anterior - tolerancia:
                falhas.append(
                    f"{metrica}@{k} regrediu: baseline={anterior:.4f}, atual={corrente:.4f}"
                )
    mrr_anterior = float(baseline["mrr"])
    mrr_atual = float(atual["mrr"])
    if mrr_atual < mrr_anterior - float(tolerancias["mrr"]):
        falhas.append(f"MRR regrediu: baseline={mrr_anterior:.4f}, atual={mrr_atual:.4f}")
    p95_anterior = float(baseline["latencia_ms"]["p95"])
    p95_atual = float(atual["latencia_ms"]["p95"])
    if p95_anterior > 0 and p95_atual > p95_anterior * (1 + float(tolerancias["p95"])):
        falhas.append(f"p95 regrediu: baseline={p95_anterior:.2f}ms, atual={p95_atual:.2f}ms")
    return falhas


def main() -> None:
    parser = argparse.ArgumentParser(description="Avalia e protege o ranking jurídico da Busca V4")
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--limite", type=int, default=max(KS_PADRAO))
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--limiares", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--permitir-pendente", action="store_true")
    args = parser.parse_args()
    resultado = asyncio.run(
        avaliar(args.arquivo, args.limite, permitir_pendente=args.permitir_pendente)
    )
    texto = json.dumps(resultado, ensure_ascii=False, indent=2)
    if args.saida:
        args.saida.write_text(texto + "\n", encoding="utf-8")
    print(texto)
    if args.gate:
        if not args.baseline or not args.limiares:
            parser.error("--gate exige --baseline e --limiares")
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        limites = json.loads(args.limiares.read_text(encoding="utf-8"))
        falhas = avaliar_regressao(resultado, baseline, limites)
        if falhas:
            raise SystemExit("Regressão da busca bloqueada:\n- " + "\n- ".join(falhas))


if __name__ == "__main__":
    main()

from collections.abc import Iterable


def avaliar_benchmark(casos: Iterable[dict]) -> dict:
    casos = list(casos)
    metricas: dict[str, float] = {}
    for k in (5, 10, 20):
        precisao, recall = [], []
        for caso in casos:
            relevantes = set(caso.get("relevantes", []))
            retornados = list(caso.get("retornados", []))[:k]
            acertos = len(relevantes.intersection(retornados))
            precisao.append(acertos / k)
            recall.append(acertos / len(relevantes) if relevantes else 0.0)
        metricas[f"precision_at_{k}"] = round(sum(precisao) / len(precisao), 4) if precisao else 0.0
        metricas[f"recall_at_{k}"] = round(sum(recall) / len(recall), 4) if recall else 0.0
    mrr = []
    falsos_negativos = 0
    for caso in casos:
        relevantes = set(caso.get("relevantes", []))
        retornados = list(caso.get("retornados", []))
        posicoes = [i + 1 for i, item in enumerate(retornados) if item in relevantes]
        mrr.append(1 / posicoes[0] if posicoes else 0.0)
        falsos_negativos += int(bool(relevantes) and not relevantes.intersection(retornados[:20]))
    metricas["mrr"] = round(sum(mrr) / len(mrr), 4) if mrr else 0.0
    metricas["falso_negativo_critico"] = falsos_negativos
    metricas["casos"] = len(casos)
    return metricas

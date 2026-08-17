from collections.abc import Iterable
import math


def avaliar_gate_regressao(metricas: dict, baseline: dict | None, tolerancia: float = 0.0) -> dict:
    if not baseline:
        return {"bloqueado": False, "regressoes": []}
    regressoes = []
    for chave in ("recall_at_5", "recall_at_10", "recall_at_20", "mrr"):
        if chave in baseline and float(metricas.get(chave, 0)) < float(baseline[chave]):
            regressoes.append({"metrica": chave, "baseline": baseline[chave], "atual": metricas.get(chave, 0)})
    return {"bloqueado": bool(regressoes), "regressoes": regressoes}


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
    metricas["falsos_negativos_criticos"] = falsos_negativos
    metricas["revisao_humana_obrigatoria"] = True
    metricas["natureza"] = "triagem_tecnica_de_anterioridades"
    latencias = sorted(float(caso["latencia_ms"]) for caso in casos if caso.get("latencia_ms") is not None)
    for nome, percentil in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
        if latencias:
            indice = min(len(latencias) - 1, math.ceil((len(latencias) - 1) * percentil))
            metricas[nome] = latencias[indice]
    metricas["casos"] = len(casos)
    return metricas

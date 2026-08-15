import json

import pytest

from app.cli.avaliar_busca_marcas import (
    avaliar_regressao,
    carregar_dataset,
    metricas_em_k,
    metricas_resultado,
    percentil,
    reciprocal_rank,
)


def test_metricas_do_benchmark_calculam_recall_e_precision() -> None:
    recall, precision = metricas_resultado(["1", "2", "3", "4"], {"2", "4", "5"})

    assert recall == 2 / 3
    assert precision == 1 / 2


def test_metricas_em_5_10_20_e_mrr() -> None:
    encontrados = [str(numero) for numero in range(1, 25)]
    esperados = {"2", "8", "21"}

    metricas = metricas_em_k(encontrados, esperados)

    assert metricas["5"] == {"recall": 1 / 3, "precision": 1 / 5}
    assert metricas["10"] == {"recall": 2 / 3, "precision": 2 / 10}
    assert metricas["20"] == {"recall": 2 / 3, "precision": 2 / 20}
    assert reciprocal_rank(encontrados, esperados) == 1 / 2


def test_percentis_interpolam_amostra() -> None:
    latencias = [10, 20, 30, 40, 50]

    assert percentil(latencias, 0.50) == 30
    assert percentil(latencias, 0.95) == pytest.approx(48)
    assert percentil(latencias, 0.99) == pytest.approx(49.6)


def test_dataset_pendente_nao_pode_ser_baseline_aprovada(tmp_path) -> None:
    caminho = tmp_path / "dataset.json"
    caminho.write_text(
        json.dumps(
            {
                "dataset_version": "v1",
                "revisao": {"status": "pendente_revisao_especialista"},
                "casos": [
                    {
                        "id": "caso-1",
                        "marca": "ACME",
                        "processos_esperados": ["1"],
                        "processos_criticos": ["1"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="não foi aprovado"):
        carregar_dataset(caminho)
    assert carregar_dataset(caminho, permitir_pendente=True)["dataset_version"] == "v1"


def _relatorio(*, recall: float = 1.0, precision: float = 0.2, mrr: float = 1.0) -> dict:
    return {
        "metricas": {
            str(k): {"recall": recall, "precision": precision} for k in (5, 10, 20)
        },
        "mrr": mrr,
        "latencia_ms": {"p95": 100.0},
        "falsos_negativos_criticos": 0,
    }


def _limites() -> dict:
    return {
        "falsos_negativos_criticos_maximos": 0,
        "regressao_maxima": {
            **{f"recall_at_{k}": 0.0 for k in (5, 10, 20)},
            **{f"precision_at_{k}": 0.02 for k in (5, 10, 20)},
            "mrr": 0.0,
            "p95": 0.25,
        },
    }


def test_gate_bloqueia_recall_mrr_latencia_e_falso_negativo_critico() -> None:
    baseline = _relatorio()
    atual = _relatorio(recall=0.8, mrr=0.5)
    atual["latencia_ms"]["p95"] = 130.0
    atual["falsos_negativos_criticos"] = 1

    falhas = avaliar_regressao(atual, baseline, _limites())

    assert any("recall@5" in falha for falha in falhas)
    assert any("MRR" in falha for falha in falhas)
    assert any("p95" in falha for falha in falhas)
    assert any("falsos negativos críticos" in falha for falha in falhas)


def test_gate_aceita_variacao_de_precision_dentro_do_limite() -> None:
    baseline = _relatorio(precision=0.20)
    atual = _relatorio(precision=0.19)

    assert avaliar_regressao(atual, baseline, _limites()) == []

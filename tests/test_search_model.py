import pytest

from app.search_model import gate_publicacao_busca, validar_transicao_status


def test_gate_bloqueia_qualquer_queda_de_recall() -> None:
    resultado = gate_publicacao_busca(
        {"recall_at_5": 0.89, "recall_at_10": 1.0, "recall_at_20": 1.0},
        {"recall_at_5": 0.90, "recall_at_10": 1.0, "recall_at_20": 1.0},
        revisoes_humanas=2,
    )
    assert resultado["bloqueado"] is True
    assert "recall_at_5 regrediu" in resultado["regressoes"]


def test_gate_exige_validacao_humana_e_bloqueia_falso_negativo() -> None:
    resultado = gate_publicacao_busca(
        {
            "recall_at_5": 1.0,
            "recall_at_10": 1.0,
            "recall_at_20": 1.0,
            "falsos_negativos_criticos": 1,
        },
        None,
        revisoes_humanas=0,
    )
    assert resultado["bloqueado"] is True


def test_transicoes_de_modelo_sao_restritas() -> None:
    validar_transicao_status("SHADOW", "VALIDATION")
    validar_transicao_status("VALIDATION", "ACTIVE")
    with pytest.raises(ValueError):
        validar_transicao_status("SHADOW", "ACTIVE")

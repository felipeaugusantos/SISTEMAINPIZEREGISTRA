from app.trademarks.benchmark import avaliar_gate_regressao
from app.trademarks.visual_ranking import calcular_score_visual


def test_score_visual_explicavel_e_versionado() -> None:
    score = calcular_score_visual(0.9, 0.5, 1.0)
    assert score.total > 0
    assert score.versao == "visual-ranking-1.0"
    assert {item["fator"] for item in score.fatores} == {"similaridade_visual", "ocr", "v viena"}


def test_gate_bloqueia_queda_de_recall() -> None:
    gate = avaliar_gate_regressao(
        {"recall_at_5": 0.4, "recall_at_10": 0.8, "recall_at_20": 1.0, "mrr": 0.7},
        {"recall_at_5": 0.5, "recall_at_10": 0.8, "recall_at_20": 1.0, "mrr": 0.7},
    )
    assert gate["bloqueado"] is True

"""Regressions for public activity descriptions, without real contact data."""
import pytest

from app.trademarks.nice import mapear_atividade


@pytest.mark.parametrize("atividade", [
    "Fabricação e comercialização de pães recheados com doces.",
    "Pães, doces; chocolates!",
    "Panificação e confeitaria.",
])
def test_panificacao_identifica_produto_nice_30(atividade):
    codigos = [classe.codigo for classe in mapear_atividade(atividade)]
    assert "30" in codigos
    assert "40" not in codigos


@pytest.mark.parametrize("atividade", [
    "Fabricação de pães sob encomenda para terceiros.",
    "Fabricação por encomenda de chocolates.",
    "Fabricação de pães para terceiros.",
])
def test_fabricacao_sob_encomenda_preserva_classe_40(atividade):
    codigos = [classe.codigo for classe in mapear_atividade(atividade)]
    assert "30" in codigos
    assert "40" in codigos


def test_pontuacao_nao_altera_ordem_das_classes():
    classes = mapear_atividade("Café, pães e doces. Loja de alimentos.")
    assert classes[0].codigo == "30"
    assert "35" in [classe.codigo for classe in classes]

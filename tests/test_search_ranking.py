import json
from pathlib import Path

from app.search_ranking import (
    VERSAO_RANKING,
    adicionar_contexto_score,
    calcular_score_nominativo,
    configuracao_ranking,
)


def test_nome_identico_supera_aproximacao_e_expoe_fatores() -> None:
    exato = calcular_score_nominativo(
        criterios=["Nome idêntico", "Elemento do nome", "Radical semelhante"],
        similaridade=1.0,
        processo="123456789",
        titulo="ACME",
    )
    aproximado = calcular_score_nominativo(
        criterios=["Radical semelhante", "Semelhança global do nome"],
        similaridade=0.70,
        processo="987654321",
        titulo="ACMEX",
    )

    assert exato.total > aproximado.total
    assert exato.versao == VERSAO_RANKING
    assert all(fator.evidencia["processo"] == "123456789" for fator in exato.fatores)


def test_contexto_so_adiciona_regras_auditaveis_e_limita_score() -> None:
    nominativo = calcular_score_nominativo(
        criterios=["Nome idêntico", "Elemento do nome", "Radical semelhante"],
        similaridade=1.0,
        processo="123456789",
        titulo="ACME",
        mesma_classe="35",
        situacao_ativa=True,
    )

    completo = adicionar_contexto_score(
        nominativo,
        processo="123456789",
        classes_atividade=["35"],
        classes_processo=["35"],
        afinidade_nivel="identica",
        afinidade_revisao="nao_aplicavel",
        situacao_ativa=True,
        alto_renome=True,
    )

    regras = [fator.regra for fator in completo.fatores]
    assert completo.total == 100
    assert regras.count("MESMA_CLASSE") == 1
    assert regras.count("SITUACAO_ATIVA") == 1
    assert "ALTO_RENOME" in regras


def test_configuracao_declara_que_score_nao_e_probabilidade() -> None:
    configuracao = configuracao_ranking()

    assert "nao representa risco ou probabilidade" in configuracao["escopo"]


def test_contrato_versionado_bloqueia_mudanca_acidental_dos_pesos() -> None:
    contrato = json.loads(
        Path("data/search-ranking-contract.v1.json").read_text(encoding="utf-8")
    )

    for caso in contrato["casos"]:
        score = calcular_score_nominativo(
            criterios=caso["criterios"],
            similaridade=caso["similaridade"],
            processo="123456789",
            titulo=caso["id"],
        )
        assert score.total == caso["score_esperado"], caso["id"]

from datetime import date

from app.prospeccao_score import PESO_PRESENCA_DIGITAL, PESO_SITUACAO_ATIVA, SCORE_MAXIMO, calcular_score

# --- Fase 5 do Radar de Prospecção (03/09/2026) -- score comercial ---------

HOJE = date(2026, 9, 3)


def test_score_sem_nenhum_dado_e_zero() -> None:
    score = calcular_score(
        situacao_cadastral=None, data_abertura=None, presenca_digital=None, triagem_marca_status=None, hoje=HOJE
    )
    assert score.total == 0.0
    assert score.fatores == ()


def test_score_soma_todos_os_fatores_positivos() -> None:
    score = calcular_score(
        situacao_cadastral="ativa",
        data_abertura=date(2016, 9, 3),  # 10 anos -- satura o fator de tempo
        presenca_digital={"ativo": True},
        triagem_marca_status="nao_localizado",
        hoje=HOJE,
    )
    assert score.total == SCORE_MAXIMO
    regras = {fator.regra for fator in score.fatores}
    assert regras == {"SITUACAO_CADASTRAL_ATIVA", "TEMPO_DE_ABERTURA", "PRESENCA_DIGITAL_ATIVA", "TRIAGEM_DE_MARCA"}


def test_score_situacao_baixada_nao_pontua() -> None:
    score = calcular_score(
        situacao_cadastral="baixada", data_abertura=None, presenca_digital=None, triagem_marca_status=None, hoje=HOJE
    )
    assert score.total == 0.0


def test_score_tempo_de_abertura_satura_em_5_anos() -> None:
    cinco_anos = calcular_score(
        situacao_cadastral=None,
        data_abertura=date(2021, 9, 3),
        presenca_digital=None,
        triagem_marca_status=None,
        hoje=HOJE,
    )
    dez_anos = calcular_score(
        situacao_cadastral=None,
        data_abertura=date(2016, 9, 3),
        presenca_digital=None,
        triagem_marca_status=None,
        hoje=HOJE,
    )
    assert cinco_anos.total == dez_anos.total


def test_score_presenca_digital_inativa_nao_pontua() -> None:
    score = calcular_score(
        situacao_cadastral=None,
        data_abertura=None,
        presenca_digital={"ativo": False},
        triagem_marca_status=None,
        hoje=HOJE,
    )
    assert score.total == 0.0
    score_sem_site = calcular_score(
        situacao_cadastral=None, data_abertura=None, presenca_digital=None, triagem_marca_status=None, hoje=HOJE
    )
    assert score.total == score_sem_site.total


def test_score_triagem_nao_localizado_pontua_mais_que_conflito_real() -> None:
    limpo = calcular_score(
        situacao_cadastral=None,
        data_abertura=None,
        presenca_digital=None,
        triagem_marca_status="nao_localizado",
        hoje=HOJE,
    )
    conflito = calcular_score(
        situacao_cadastral=None,
        data_abertura=None,
        presenca_digital=None,
        triagem_marca_status="resultado_relevante_localizado",
        hoje=HOJE,
    )
    assert limpo.total > conflito.total > 0


def test_score_nunca_ultrapassa_o_maximo() -> None:
    score = calcular_score(
        situacao_cadastral="ativa",
        data_abertura=date(2000, 1, 1),
        presenca_digital={"ativo": True},
        triagem_marca_status="nao_localizado",
        hoje=HOJE,
    )
    assert score.total <= SCORE_MAXIMO


def test_fatores_json_e_serializavel_e_auditavel() -> None:
    score = calcular_score(
        situacao_cadastral="ativa", data_abertura=None, presenca_digital=None, triagem_marca_status=None, hoje=HOJE
    )
    fatores = score.fatores_json()
    assert fatores == [{"regra": "SITUACAO_CADASTRAL_ATIVA", "peso": PESO_SITUACAO_ATIVA, "evidencia": {"situacao_cadastral": "ativa"}}]


def test_peso_presenca_digital_e_positivo() -> None:
    assert PESO_PRESENCA_DIGITAL > 0

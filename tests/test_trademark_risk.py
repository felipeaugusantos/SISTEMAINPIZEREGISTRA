from app.schemas import RelatorioMarcaResponse
from app.trademarks.risk import (
    MODO_MOTOR,
    VERSAO_MOTOR,
    ConflitoEntrada,
    calcular_risco,
    nivel_por_pontuacao,
    pontuar_conflito,
    regras_para_json,
)


def conflito(**overrides: object) -> ConflitoEntrada:
    dados = {
        "numero": "123456789",
        "titulo": "ACME",
        "criterios_encontro": ("Nome idêntico",),
        "relevancia_situacao": "ativa",
        "situacao_normalizada": "registrada",
        "afinidade_nivel": "identica",
        "afinidade_revisao": "nao_aplicavel",
        "classes_processo": ("35",),
        "alto_renome": False,
    }
    dados.update(overrides)
    return ConflitoEntrada(**dados)


def test_quatro_niveis_de_risco_sao_deterministicos() -> None:
    assert nivel_por_pontuacao(0) == "baixo"
    assert nivel_por_pontuacao(25) == "moderado"
    assert nivel_por_pontuacao(50) == "alto"
    assert nivel_por_pontuacao(75) == "critico"


def test_conflito_exato_ativo_mesma_classe_e_alto_renome_e_critico() -> None:
    resultado = pontuar_conflito(conflito(alto_renome=True))

    assert resultado.pontuacao == 100
    assert resultado.nivel == "critico"
    assert [fator.regra for fator in resultado.fatores] == [
        "semelhanca_nome",
        "situacao_processual",
        "afinidade_classes",
        "alto_renome",
    ]
    assert all(fator.evidencia["processo"] == "123456789" for fator in resultado.fatores)


def test_situacao_inativa_reduz_pontuacao_sem_ocultar_conflito() -> None:
    resultado = pontuar_conflito(
        conflito(
            relevancia_situacao="inativa",
            situacao_normalizada="extinta",
        )
    )

    assert resultado.pontuacao == 45
    assert resultado.nivel == "moderado"
    assert any(fator.pontos == -20 for fator in resultado.fatores)


def test_motor_usa_maior_conflito_e_guarda_principais() -> None:
    avaliacao = calcular_risco(
        [
            conflito(numero="111111111", alto_renome=True),
            conflito(
                numero="222222222",
                criterios_encontro=("Radical semelhante",),
                relevancia_situacao="inativa",
                afinidade_nivel="nao_mapeada",
                afinidade_revisao="pendente",
            ),
        ]
    )

    assert avaliacao.pontuacao == 100
    assert avaliacao.principais_conflitos[0].numero == "111111111"
    assert len(avaliacao.principais_conflitos) == 2


def test_regras_registram_versao_e_modo_sombra() -> None:
    regras = regras_para_json()

    assert regras["versao"] == VERSAO_MOTOR
    assert regras["modo"] == MODO_MOTOR == "sombra"
    assert regras["tipo_score"] == "RISCO_DETERMINISTICO_POR_REGRAS"
    assert "não é probabilidade" in regras["interpretacao"]


def test_risco_nao_faz_parte_do_relatorio_do_cliente() -> None:
    campos = RelatorioMarcaResponse.model_fields

    assert "pontuacao" not in campos
    assert "nivel_risco" not in campos
    assert "principais_conflitos" not in campos

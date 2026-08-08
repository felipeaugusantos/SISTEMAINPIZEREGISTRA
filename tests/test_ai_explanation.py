import json

import pytest

from app.models import AvaliacaoRiscoMarca
from app.schemas import RelatorioMarcaResponse
from app.settings import Settings
from app.trademarks.ai_explanation import (
    ConfiguracaoIAInativaError,
    ExplicacaoConflitoIA,
    SaidaExplicacaoIA,
    SaidaIAInvalidaError,
    construir_entrada_anonimizada,
    exige_revisao_humana,
    gerar_explicacao,
    hash_entrada,
    validar_saida_contra_entrada,
)


def avaliacao() -> AvaliacaoRiscoMarca:
    return AvaliacaoRiscoMarca(
        id=7,
        pesquisa_id="pesquisa-1",
        versao_motor="deterministico-1.0",
        modo="sombra",
        pontuacao=80,
        nivel="critico",
        principais_conflitos=[
            {
                "numero": "123456789",
                "titulo": "MARCA SIGILOSA PARA O MODELO",
                "pontuacao": 80,
                "nivel": "critico",
                "classes_processo": ["35", "42"],
                "fatores": [
                    {
                        "regra": "semelhanca_nome",
                        "pontos": 40,
                        "evidencia": "Nome idêntico",
                    },
                    {
                        "regra": "situacao_processual",
                        "pontos": 20,
                        "evidencia": "registrada",
                    },
                    {
                        "regra": "afinidade_classes",
                        "pontos": 20,
                        "evidencia": "alta (aprovada)",
                    },
                ],
            }
        ],
        regras_aplicadas={
            "agregacao": "maior pontuação entre os conflitos encontrados",
        },
    )


def saida_valida() -> SaidaExplicacaoIA:
    return SaidaExplicacaoIA(
        pontuacao=80,
        nivel="critico",
        resumo_executivo="A pontuação 80 indica o nível crítico calculado pelo motor.",
        leitura_pontuacao="O total decorre da combinação controlada dos fatores informados.",
        conflitos=[
            ExplicacaoConflitoIA(
                conflito_id=1,
                regras_referenciadas=[
                    "semelhanca_nome",
                    "situacao_processual",
                    "afinidade_classes",
                ],
                explicacao=(
                    "O conflito combina semelhança do nome, situação processual "
                    "e afinidade entre atividades."
                ),
            )
        ],
        limitacoes=[
            "indicativo_nao_conclusivo",
            "nao_substitui_analise_humana",
            "baseado_apenas_no_motor_deterministico",
        ],
    )


def test_entrada_da_ia_remove_pii_processos_marcas_e_classes() -> None:
    entrada = construir_entrada_anonimizada(avaliacao())
    payload = entrada.model_dump(mode="json")
    serializado = json.dumps(payload, ensure_ascii=False).lower()

    for dado_proibido in [
        "email",
        "telefone",
        "empresa",
        "lead",
        "123456789",
        "marca sigilosa",
        '"35"',
        '"42"',
    ]:
        assert dado_proibido not in serializado
    assert not ({"nome", "email", "telefone", "empresa", "marca"} & payload.keys())


def test_hash_da_entrada_e_deterministico() -> None:
    entrada = construir_entrada_anonimizada(avaliacao())

    assert hash_entrada(entrada) == hash_entrada(entrada)
    assert len(hash_entrada(entrada)) == 64


def test_risco_alto_e_critico_exigem_revisao_humana() -> None:
    assert exige_revisao_humana("alto") is True
    assert exige_revisao_humana("critico") is True
    assert exige_revisao_humana("moderado") is False
    assert exige_revisao_humana("baixo") is False


def test_saida_valida_somente_explica_regras_existentes() -> None:
    entrada = construir_entrada_anonimizada(avaliacao())
    saida = saida_valida().model_copy(
        update={
            "resumo_executivo": (
                "A pontuação 80 decorre dos fatores autorizados do conflito 1."
            )
        }
    )

    validar_saida_contra_entrada(entrada, saida)


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("resumo_executivo", "O processo 987654321 cria um risco não informado."),
        ("resumo_executivo", "A classe 25 também deveria ser considerada."),
        ("resumo_executivo", "O artigo 124 da LPI impede o registro pretendido."),
    ],
)
def test_saida_com_processo_classe_ou_fundamento_inventado_e_bloqueada(
    campo: str,
    valor: str,
) -> None:
    entrada = construir_entrada_anonimizada(avaliacao())
    saida = saida_valida().model_copy(update={campo: valor})

    with pytest.raises(SaidaIAInvalidaError):
        validar_saida_contra_entrada(entrada, saida)


def test_saida_nao_pode_alterar_pontuacao_ou_inventar_regra() -> None:
    entrada = construir_entrada_anonimizada(avaliacao())
    alterada = saida_valida().model_copy(update={"pontuacao": 70})
    with pytest.raises(SaidaIAInvalidaError):
        validar_saida_contra_entrada(entrada, alterada)

    conflito = saida_valida().conflitos[0].model_copy(
        update={"regras_referenciadas": ["alto_renome"]}
    )
    regra_inventada = saida_valida().model_copy(update={"conflitos": [conflito]})
    with pytest.raises(SaidaIAInvalidaError):
        validar_saida_contra_entrada(entrada, regra_inventada)


async def test_integracao_permanece_desativada_sem_chave() -> None:
    entrada = construir_entrada_anonimizada(avaliacao())
    settings = Settings(ai_explanations_enabled=False, openai_api_key=None)

    with pytest.raises(ConfiguracaoIAInativaError):
        await gerar_explicacao(entrada, settings)


async def test_chamada_usa_saida_estruturada_e_nao_armazena_resposta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict] = []

    class RespostasFalsas:
        async def parse(self, **kwargs: object) -> object:
            chamadas.append(kwargs)

            class Resposta:
                id = "resp_teste"
                output_parsed = saida_valida()

            return Resposta()

    class ClienteFalso:
        responses = RespostasFalsas()

    monkeypatch.setattr(
        "app.trademarks.ai_explanation.AsyncOpenAI",
        lambda **_kwargs: ClienteFalso(),
    )
    entrada = construir_entrada_anonimizada(avaliacao())
    settings = Settings(
        ai_explanations_enabled=True,
        openai_api_key="chave-de-teste",
        openai_explanation_model="gpt-5.6-luna",
    )

    resultado = await gerar_explicacao(entrada, settings)

    assert resultado.resposta_id == "resp_teste"
    assert chamadas[0]["store"] is False
    assert chamadas[0]["text_format"] is SaidaExplicacaoIA
    conteudo_enviado = json.dumps(chamadas[0]["input"], ensure_ascii=False).lower()
    assert "123456789" not in conteudo_enviado
    assert "marca sigilosa" not in conteudo_enviado


def test_ia_nao_e_exposta_no_relatorio_publico() -> None:
    campos = RelatorioMarcaResponse.model_fields

    assert "explicacao_ia" not in campos
    assert "pontuacao" not in campos
    assert "nivel_risco" not in campos

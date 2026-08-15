from datetime import date

import pytest
from pydantic import ValidationError

from app.models import (
    ClassificacaoMarca,
    ControleAprendizadoMarca,
    ModeloRegistrabilidade,
    Movimentacao,
    Processo,
    RotuloHistoricoMarca,
    TipoProcesso,
)
from app.schemas import AprendizadoModeloResponse
from app.trademarks.learning import (
    ATRIBUTOS_MODELO,
    _distribuicoes_dataset,
    _limiar_otimo,
    _metricas,
    agregar_atributos,
    decidir_exibicao_estimativa,
    extrair_atributos_par,
    extrair_rotulo,
    prever,
    prioridade_revisao_rotulo,
    validar_estimativa_para_cliente,
)
from app.trademarks.model_status import StatusModelo, normalizar_status_modelo


def test_limiar_otimo_usa_a_distribuicao_real_calibrada() -> None:
    limiar = _limiar_otimo([0.75, 0.76, 0.80, 0.90], [0, 0, 1, 1])
    assert 0.76 < limiar <= 0.80


def movimento(
    descricao: str, *, numero_rpi: int = 2900, codigo: str | None = None
) -> Movimentacao:
    item = Movimentacao(
        processo_id=1,
        codigo_despacho=codigo,
        descricao=descricao,
        data_rpi=date(2026, 7, 14),
        numero_rpi=numero_rpi,
        fonte_arquivo="teste.xml",
        chave_origem=f"teste-{numero_rpi}-{descricao}",
    )
    item.id = numero_rpi
    return item


def test_rotulo_separa_decisao_de_merito_de_arquivamento_formal() -> None:
    assert extrair_rotulo([movimento("Arquivamento por falta de pagamento")]) is None
    resultado = extrair_rotulo([movimento("Indeferimento do pedido por falta de distintividade")])
    assert resultado is not None
    assert resultado.alvo_deferimento is False
    assert resultado.fundamento == "falta_distintividade"


def test_recurso_nao_substitui_decisao_final() -> None:
    resultado = extrair_rotulo(
        [
            movimento("Deferimento do pedido", numero_rpi=2890),
            movimento("Recurso contra o indeferimento", numero_rpi=2891),
        ]
    )
    assert resultado is not None
    assert resultado.alvo_deferimento is True


def test_recurso_provido_reforma_resultado_para_deferimento() -> None:
    resultado = extrair_rotulo(
        [
            movimento("Indeferimento do pedido", numero_rpi=2890, codigo="IPAS024"),
            movimento(
                "Recurso provido (decisão reformada para: Deferimento)",
                numero_rpi=2900,
                codigo="IPAS237",
            ),
        ]
    )
    assert resultado is not None
    assert resultado.alvo_deferimento is True
    assert resultado.fundamento == "deferimento_recurso"


def test_rotulo_generico_prioriza_revisao_e_preserva_evidencias() -> None:
    resultado = extrair_rotulo(
        [
            movimento("Notificação de oposição", numero_rpi=2880, codigo="IPAS423"),
            movimento("Indeferimento do pedido", numero_rpi=2890, codigo="IPAS024"),
        ]
    )
    assert resultado is not None
    assert resultado.fundamento == "indeferimento_nao_especificado"
    assert prioridade_revisao_rotulo(
        resultado.fundamento, resultado.confianca, resultado.rotulo
    ) == "alta"
    assert {item["tipo"] for item in resultado.evidencias} == {"decisao", "oposicao"}


def test_atributos_capturam_fonetica_e_classe_sem_llm() -> None:
    atributos = extrair_atributos_par(
        "PHIRMINO",
        "FIRMINO",
        ["35"],
        ["35"],
        afinidade_conhecida=False,
        candidata_ativa=True,
    )
    assert atributos["fonetica_igual"] == 1
    assert atributos["fonetica_similaridade"] == 1
    assert atributos["prefixo_radical"] == 1
    assert atributos["classe_identica"] == 1
    assert atributos["candidato_ativo"] == 1


def test_agregacao_preserva_maior_conflito_e_volume() -> None:
    agregado = agregar_atributos(
        [
            {"similaridade_sequencia": 0.4, "classe_identica": 1.0},
            {"similaridade_sequencia": 0.9},
        ]
    )
    assert agregado["similaridade_sequencia"] == 0.9
    assert agregado["classe_identica"] == 1.0
    assert agregado["quantidade_candidatos_norm"] == 0.2
    assert agregado["similaridade_top3_media"] == 0.65


def test_modelo_antigo_permanece_compativel_apos_novos_atributos() -> None:
    nomes_antigos = ["similaridade_sequencia", "classe_identica"]
    parametros = {
        "pesos": {nome: 0.0 for nome in nomes_antigos},
        "vies": 0.0,
        "medias": {nome: 0.0 for nome in nomes_antigos},
        "desvios": {nome: 1.0 for nome in nomes_antigos},
    }
    modelo = ModeloRegistrabilidade(
        versao="legado-atributos-1.0",
        status=StatusModelo.ACTIVE.value,
        atributos=nomes_antigos,
        parametros=parametros,
        calibracao={"a": 1.0, "b": 0.0},
        metricas={"brier": 0.2},
        dataset={},
    )

    resultado = prever({"similaridade_sequencia": 0.8, "nome_identico": 1.0}, modelo)

    assert resultado.probabilidade == 0.5


def test_predicao_e_versionada_e_explica_fatores() -> None:
    parametros = {
        "pesos": {nome: 0.0 for nome in ATRIBUTOS_MODELO},
        "vies": 0.0,
        "medias": {nome: 0.0 for nome in ATRIBUTOS_MODELO},
        "desvios": {nome: 1.0 for nome in ATRIBUTOS_MODELO},
    }
    parametros["pesos"]["similaridade_sequencia"] = -2.0
    modelo = ModeloRegistrabilidade(
        versao="teste-1",
        status=StatusModelo.ACTIVE.value,
        atributos=list(ATRIBUTOS_MODELO),
        parametros=parametros,
        calibracao={"a": 1.0, "b": 0.0},
        metricas={"brier": 0.2},
        dataset={},
    )
    entrada = {nome: 0.0 for nome in ATRIBUTOS_MODELO}
    entrada["similaridade_sequencia"] = 1.0
    resultado = prever(entrada, modelo)
    assert resultado.probabilidade < 0.5
    assert resultado.nivel in {"alto_risco", "critico"}
    assert resultado.fatores[0]["atributo"] == "similaridade_sequencia"
    assert resultado.fatores[0]["valor_entrada"] == 1.0
    assert resultado.fatores[0]["media_referencia"] == 0.0
    assert resultado.fatores[0]["peso_modelo"] == -2.0
    assert resultado.probabilidade_inferior <= resultado.probabilidade
    assert resultado.probabilidade_superior >= resultado.probabilidade


def test_estimativa_so_e_elegivel_com_intervalo_e_governanca_validos() -> None:
    parametros = {
        "pesos": {nome: 0.0 for nome in ATRIBUTOS_MODELO},
        "vies": 0.4,
        "medias": {nome: 0.0 for nome in ATRIBUTOS_MODELO},
        "desvios": {nome: 1.0 for nome in ATRIBUTOS_MODELO},
    }
    parametros["bootstrap_modelos"] = [
        {**parametros, "vies": 0.30 + indice * 0.01} for indice in range(12)
    ]
    modelo = ModeloRegistrabilidade(
        versao="teste-governanca",
        status=StatusModelo.ACTIVE.value,
        atributos=list(ATRIBUTOS_MODELO),
        parametros=parametros,
        calibracao={"a": 1.0, "b": 0.0},
        metricas={"recall": 0.9, "especificidade": 0.85, "brier": 0.12, "ece": 0.05},
        dataset={
            "total": 500,
            "teste": 75,
            "bootstrap_modelos": 12,
            "distribuicao_por_classe": {"35": {"total": 500}},
            "distribuicao_por_periodo": {"2025": {"total": 500}},
        },
    )
    controle = ControleAprendizadoMarca(
        minimo_revisoes_humanas=30,
        minimo_recall=0.8,
        minimo_especificidade=0.7,
        maximo_brier=0.25,
        maximo_ece=0.12,
        minimo_amostras_modelo=300,
        minimo_amostras_teste=50,
        largura_maxima_intervalo=0.35,
        minima_cobertura=0.4,
    )
    resultado = prever({nome: 0.0 for nome in ATRIBUTOS_MODELO}, modelo)

    assert resultado.probabilidade_inferior < resultado.probabilidade_superior
    assert resultado.confianca_rotulo in {"media", "alta"}
    assert validar_estimativa_para_cliente(resultado, modelo, controle, 30) == []


def test_modelo_antigo_sem_bootstrap_fica_restrito_ao_modo_sombra() -> None:
    parametros = {
        "pesos": {nome: 0.0 for nome in ATRIBUTOS_MODELO},
        "vies": 0.0,
        "medias": {nome: 0.0 for nome in ATRIBUTOS_MODELO},
        "desvios": {nome: 1.0 for nome in ATRIBUTOS_MODELO},
    }
    modelo = ModeloRegistrabilidade(
        versao="legado",
        status=StatusModelo.ACTIVE.value,
        atributos=list(ATRIBUTOS_MODELO),
        parametros=parametros,
        calibracao={"a": 1.0, "b": 0.0},
        metricas={"recall": 1.0, "especificidade": 1.0, "brier": 0.1, "ece": 0.01},
        dataset={"total": 1000, "teste": 150, "bootstrap_modelos": 0},
    )
    controle = ControleAprendizadoMarca(
        minimo_revisoes_humanas=10,
        minimo_recall=0.8,
        minimo_especificidade=0.7,
        maximo_brier=0.25,
        maximo_ece=0.12,
        minimo_amostras_modelo=300,
        minimo_amostras_teste=50,
        largura_maxima_intervalo=0.8,
        minima_cobertura=0,
    )
    resultado = prever({nome: 0.0 for nome in ATRIBUTOS_MODELO}, modelo)

    bloqueios = validar_estimativa_para_cliente(resultado, modelo, controle, 10)
    assert "Modelo sem intervalo bootstrap válido" in bloqueios


def test_gate_tecnico_bloqueia_estimativa_sem_exigir_revisao_humana() -> None:
    controle = ControleAprendizadoMarca(exibir_cliente=True)
    alertas = ["Especificidade do teste abaixo do mínimo"]

    modo, elegivel, alertas_retornados = decidir_exibicao_estimativa(
        controle, alertas, modelo_status=StatusModelo.ACTIVE.value
    )

    assert modo == "sombra"
    assert elegivel is False
    assert alertas_retornados == alertas


def test_modelo_active_aprovado_nos_gates_pode_ser_exibido() -> None:
    controle = ControleAprendizadoMarca(exibir_cliente=True)

    modo, elegivel, alertas = decidir_exibicao_estimativa(
        controle, [], modelo_status=StatusModelo.ACTIVE.value
    )

    assert modo == "cliente"
    assert elegivel is True
    assert alertas == []


def test_shadow_nunca_e_exibido_mesmo_sem_alertas() -> None:
    controle = ControleAprendizadoMarca(exibir_cliente=True)

    modo, elegivel, alertas = decidir_exibicao_estimativa(
        controle, [], modelo_status=StatusModelo.SHADOW.value
    )

    assert (modo, elegivel, alertas) == ("sombra", False, [])


def test_calibracao_expoe_distribuicao_por_faixa() -> None:
    metricas = _metricas([0.1, 0.3, 0.7, 0.9], [0, 0, 1, 1], limiar=0.5)

    assert metricas["brier"] < 0.1
    assert metricas["ece"] >= 0
    assert sum(faixa["amostras"] for faixa in metricas["calibracao_por_faixa"]) == 4
    assert all("taxa_observada" in faixa for faixa in metricas["calibracao_por_faixa"])


def test_estados_legados_sao_normalizados_conservadoramente() -> None:
    assert normalizar_status_modelo("ativo") is StatusModelo.ACTIVE
    assert normalizar_status_modelo("candidato") is StatusModelo.SHADOW
    assert normalizar_status_modelo("reprovado") is StatusModelo.DISABLED
    assert normalizar_status_modelo("desconhecido") is StatusModelo.DISABLED


def test_predicao_e_reproduzivel_para_mesmo_modelo_e_entrada() -> None:
    parametros = {
        "pesos": {nome: 0.1 for nome in ATRIBUTOS_MODELO},
        "vies": -0.2,
        "medias": {nome: 0.25 for nome in ATRIBUTOS_MODELO},
        "desvios": {nome: 1.0 for nome in ATRIBUTOS_MODELO},
    }
    parametros["bootstrap_modelos"] = [
        {**parametros, "vies": -0.3 + indice / 100} for indice in range(12)
    ]
    modelo = ModeloRegistrabilidade(
        versao="reprodutivel-1",
        status=StatusModelo.ACTIVE.value,
        atributos=list(ATRIBUTOS_MODELO),
        parametros=parametros,
        calibracao={"a": 0.9, "b": 0.05},
        metricas={"ece": 0.04},
        dataset={},
    )
    entrada = {nome: 0.5 for nome in ATRIBUTOS_MODELO}

    assert prever(entrada, modelo) == prever(entrada, modelo)


def test_dataset_documenta_distribuicao_por_classe_e_periodo() -> None:
    processo = Processo(
        id=1,
        numero="900000001",
        numero_normalizado="900000001",
        tipo=TipoProcesso.MARCA,
        titulo="MARCA TESTE",
        fonte="teste",
        classificacoes=[
            ClassificacaoMarca(sistema="nice", codigo="35"),
            ClassificacaoMarca(sistema="nice", codigo="42"),
        ],
    )
    rotulo = RotuloHistoricoMarca(
        id=1,
        processo_id=1,
        rotulo="deferida",
        alvo_deferimento=True,
        fundamento="deferimento",
        data_referencia=date(2025, 7, 1),
    )

    por_classe, por_periodo = _distribuicoes_dataset({1: (rotulo, processo, [])})

    assert por_classe == {
        "35": {"total": 1, "deferidas": 1, "indeferidas": 0},
        "42": {"total": 1, "deferidas": 1, "indeferidas": 0},
    }
    assert por_periodo == {
        "2025": {"total": 1, "deferidas": 1, "indeferidas": 0}
    }


def test_schema_publico_rejeita_status_legado_do_modelo() -> None:
    with pytest.raises(ValidationError):
        AprendizadoModeloResponse(
            id=1,
            versao="legado",
            algoritmo="regressao_logistica",
            status="ativo",
            metricas={},
            dataset={},
            corte_treino=None,
            corte_validacao=None,
            treinado_em="2026-08-14T12:00:00Z",
            ativado_em=None,
            ativado_por=None,
        )


def test_risco_deterministico_nao_e_feature_da_estimativa_historica() -> None:
    assert "risco_pontuacao" not in ATRIBUTOS_MODELO
    assert "risco_nivel" not in ATRIBUTOS_MODELO

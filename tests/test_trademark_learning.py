from datetime import date

from app.models import ControleAprendizadoMarca, ModeloRegistrabilidade, Movimentacao
from app.trademarks.learning import (
    ATRIBUTOS_MODELO,
    agregar_atributos,
    decidir_exibicao_estimativa,
    extrair_atributos_par,
    extrair_rotulo,
    prever,
    prioridade_revisao_rotulo,
    validar_estimativa_para_cliente,
)


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
        status="ativo",
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
        status="ativo",
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
        status="ativo",
        atributos=list(ATRIBUTOS_MODELO),
        parametros=parametros,
        calibracao={"a": 1.0, "b": 0.0},
        metricas={"recall": 0.9, "especificidade": 0.85, "brier": 0.12, "ece": 0.05},
        dataset={"total": 500, "teste": 75, "bootstrap_modelos": 12},
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
        status="ativo",
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

    modo, elegivel, alertas_retornados = decidir_exibicao_estimativa(controle, alertas)

    assert modo == "sombra"
    assert elegivel is False
    assert alertas_retornados == alertas


def test_estimativa_aprovada_aparece_sem_revisao_humana() -> None:
    controle = ControleAprendizadoMarca(exibir_cliente=True)

    modo, elegivel, alertas = decidir_exibicao_estimativa(controle, [])

    assert modo == "cliente"
    assert elegivel is True
    assert alertas == []

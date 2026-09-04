from datetime import date

from app.rfb_cnpj import (
    cnaes_secundarios_para_lista,
    montar_cnpj,
    montar_registro_cache,
    normalizar_cidade,
    normalizar_data_rfb,
    normalizar_porte,
    normalizar_situacao_cadastral,
)

# --- Fase 2 do Radar de Prospecção (03/09/2026) -- normalização do layout RFB ---


def test_normalizar_situacao_cadastral_mapeia_codigos_conhecidos() -> None:
    assert normalizar_situacao_cadastral("02") == "ativa"
    assert normalizar_situacao_cadastral("08") == "baixada"
    assert normalizar_situacao_cadastral("04") == "inapta"


def test_normalizar_situacao_cadastral_codigo_desconhecido_retorna_none() -> None:
    assert normalizar_situacao_cadastral("99") is None
    assert normalizar_situacao_cadastral(None) is None


def test_normalizar_porte_mapeia_codigos_conhecidos() -> None:
    assert normalizar_porte("01") == "micro"
    assert normalizar_porte("03") == "pequeno"
    assert normalizar_porte("05") == "demais"
    assert normalizar_porte("00") == "nao_informado"


def test_normalizar_cidade_remove_acentos_e_padroniza_espacos() -> None:
    assert normalizar_cidade("  Ribeirão   Preto ") == "RIBEIRAO PRETO"
    assert normalizar_cidade("São José d'Ávila") == "SAO JOSE D'AVILA"


def test_normalizar_cidade_vazia_retorna_none() -> None:
    assert normalizar_cidade(None) is None
    assert normalizar_cidade("   ") is None


def test_montar_cnpj_junta_basico_ordem_dv() -> None:
    assert montar_cnpj("11222333", "0001", "81") == "11222333000181"


def test_montar_cnpj_preenche_zeros_a_esquerda() -> None:
    assert montar_cnpj("1", "1", "5") == "00000001000105"


def test_montar_cnpj_invalido_retorna_none() -> None:
    assert montar_cnpj("", "", "") is None
    assert montar_cnpj("abc", "0001", "81") is None


def test_normalizar_data_rfb_formato_valido() -> None:
    assert normalizar_data_rfb("20200315") == date(2020, 3, 15)


def test_normalizar_data_rfb_vazia_ou_zerada_retorna_none() -> None:
    assert normalizar_data_rfb("") is None
    assert normalizar_data_rfb("00000000") is None
    assert normalizar_data_rfb(None) is None


def test_normalizar_data_rfb_malformada_retorna_none() -> None:
    assert normalizar_data_rfb("20201399") is None


def test_cnaes_secundarios_para_lista_separa_por_virgula() -> None:
    assert cnaes_secundarios_para_lista("4711302,4712100") == ["4711302", "4712100"]
    assert cnaes_secundarios_para_lista("") == []
    assert cnaes_secundarios_para_lista(None) == []


def _estabelecimento(**kwargs: str) -> dict[str, str]:
    base = {
        "cnpj_basico": "11222333",
        "cnpj_ordem": "0001",
        "cnpj_dv": "81",
        "nome_fantasia": "Loja Exemplo",
        "situacao_cadastral": "02",
        "data_inicio_atividade": "20200315",
        "cnae_fiscal_principal": "4711302",
        "cnae_fiscal_secundaria": "4712100",
        "uf": "SP",
        "municipio": "7107",
        "ddd_1": "11",
        "telefone_1": "988887777",
        "correio_eletronico": "Contato@Loja.Com.Br",
    }
    base.update(kwargs)
    return base


def test_montar_registro_cache_monta_registro_completo() -> None:
    registro = montar_registro_cache(
        _estabelecimento(), porte_empresa="01", razao_social="Loja Exemplo Ltda", municipios={"7107": "SAO PAULO"}
    )

    assert registro == {
        "cnpj": "11222333000181",
        "razao_social": "Loja Exemplo Ltda",
        "nome_fantasia": "Loja Exemplo",
        "cnae_principal": "4711302",
        "cnaes_secundarios": ["4712100"],
        "porte": "micro",
        "situacao_cadastral": "ativa",
        "data_abertura": date(2020, 3, 15),
        "uf": "SP",
        "cidade": "SAO PAULO",
        "telefone": "11988887777",
        "email": "contato@loja.com.br",
    }


def test_montar_registro_cache_normaliza_nome_acentuado_do_municipio() -> None:
    registro = montar_registro_cache(
        _estabelecimento(),
        porte_empresa="03",
        razao_social="Empresa Ribeirão Ltda",
        municipios={"7107": "Ribeirão Preto"},
    )

    assert registro["cidade"] == "RIBEIRAO PRETO"


def test_montar_registro_cache_cnpj_invalido_retorna_none() -> None:
    registro = montar_registro_cache(
        _estabelecimento(cnpj_basico="", cnpj_ordem="", cnpj_dv=""),
        porte_empresa="01",
        razao_social="Loja Exemplo Ltda",
        municipios={},
    )
    assert registro is None


def test_montar_registro_cache_municipio_desconhecido_fica_none() -> None:
    registro = montar_registro_cache(
        _estabelecimento(municipio="9999"), porte_empresa=None, razao_social="Loja Exemplo Ltda", municipios={}
    )
    assert registro["cidade"] is None
    assert registro["porte"] is None

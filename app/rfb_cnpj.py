"""Layout dos Dados Abertos do CNPJ (Receita Federal) e normalização para
o cache_estabelecimentos_rfb usado pelo Radar de Prospecção (Fase 2,
03/09/2026, docs/arquitetura-radar-prospeccao-2026-09-03.md).

Layout verificado em 03/09/2026 contra a documentação oficial
(https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf) e o projeto de
referência da comunidade (aphonsoar/Receita_Federal_do_Brasil_-_Dados_Publicos_CNPJ).
Arquivos são CSV sem cabeçalho, delimitador ";", codificação latin-1.

Este módulo contém só as funções PURAS (parsing/normalização de uma linha
já lida) -- testáveis sem baixar nada da RFB. O download e a orquestração
do ETL completo ficam em app/cli/importar_cnpj_rfb.py.
"""

from datetime import date

# Ordem exata das colunas de cada arquivo (sem cabeçalho na origem).
COLUNAS_EMPRESA = (
    "cnpj_basico",
    "razao_social",
    "natureza_juridica",
    "qualificacao_responsavel",
    "capital_social",
    "porte_empresa",
    "ente_federativo_responsavel",
)
COLUNAS_ESTABELECIMENTO = (
    "cnpj_basico",
    "cnpj_ordem",
    "cnpj_dv",
    "identificador_matriz_filial",
    "nome_fantasia",
    "situacao_cadastral",
    "data_situacao_cadastral",
    "motivo_situacao_cadastral",
    "nome_cidade_exterior",
    "pais",
    "data_inicio_atividade",
    "cnae_fiscal_principal",
    "cnae_fiscal_secundaria",
    "tipo_logradouro",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "uf",
    "municipio",
    "ddd_1",
    "telefone_1",
    "ddd_2",
    "telefone_2",
    "ddd_fax",
    "fax",
    "correio_eletronico",
    "situacao_especial",
    "data_situacao_especial",
)
COLUNAS_REFERENCIA = ("codigo", "descricao")  # cnae, municipio, motivo, natureza_juridica, pais, qualificacao

# Situação cadastral (Art. 39, IN RFB 2.119/2022).
_SITUACAO_CADASTRAL = {
    "01": "nula",
    "02": "ativa",
    "03": "suspensa",
    "04": "inapta",
    "08": "baixada",
}

# Porte da empresa.
_PORTE_EMPRESA = {
    "00": "nao_informado",
    "01": "micro",
    "03": "pequeno",
    "05": "demais",
}


def normalizar_situacao_cadastral(codigo: str | None) -> str | None:
    return _SITUACAO_CADASTRAL.get((codigo or "").strip())


def normalizar_porte(codigo: str | None) -> str | None:
    return _PORTE_EMPRESA.get((codigo or "").strip())


def montar_cnpj(cnpj_basico: str, cnpj_ordem: str, cnpj_dv: str) -> str | None:
    cnpj_basico = (cnpj_basico or "").strip()
    cnpj_ordem = (cnpj_ordem or "").strip()
    cnpj_dv = (cnpj_dv or "").strip()
    if not cnpj_basico or not cnpj_ordem or not cnpj_dv:
        return None
    digitos = f"{cnpj_basico:>08}{cnpj_ordem:>04}{cnpj_dv:>02}"
    if len(digitos) != 14 or not digitos.isdigit():
        return None
    return digitos


def normalizar_data_rfb(valor: str | None) -> date | None:
    """Datas da RFB vêm como AAAAMMDD (string) ou vazias/"00000000" quando ausentes."""
    valor = (valor or "").strip()
    if not valor or valor == "00000000" or len(valor) != 8:
        return None
    try:
        return date(int(valor[:4]), int(valor[4:6]), int(valor[6:8]))
    except ValueError:
        return None


def formatar_cnae(codigo: str | None) -> str | None:
    codigo = (codigo or "").strip()
    return codigo or None


def cnaes_secundarios_para_lista(valor: str | None) -> list[str]:
    """cnae_fiscal_secundaria vem como códigos separados por vírgula, ex.: '4711302,4712100'."""
    if not valor:
        return []
    return [item.strip() for item in valor.split(",") if item.strip()]


def montar_registro_cache(
    estabelecimento: dict[str, str],
    porte_empresa: str | None,
    razao_social: str,
    municipios: dict[str, str],
) -> dict | None:
    """Junta uma linha de Estabelecimentos com o porte/razão social vindos de
    Empresas (join por cnpj_basico, feito por quem chama) e devolve o dict
    pronto para upsert em cache_estabelecimentos_rfb. None se o CNPJ
    calculado for inválido (linha corrompida) -- descartada, não quebra o ETL."""
    cnpj = montar_cnpj(
        estabelecimento["cnpj_basico"], estabelecimento["cnpj_ordem"], estabelecimento["cnpj_dv"]
    )
    if cnpj is None:
        return None
    return {
        "cnpj": cnpj,
        "razao_social": razao_social.strip(),
        "nome_fantasia": (estabelecimento.get("nome_fantasia") or "").strip() or None,
        "cnae_principal": formatar_cnae(estabelecimento.get("cnae_fiscal_principal")),
        "cnaes_secundarios": cnaes_secundarios_para_lista(estabelecimento.get("cnae_fiscal_secundaria")),
        "porte": normalizar_porte(porte_empresa),
        "situacao_cadastral": normalizar_situacao_cadastral(estabelecimento.get("situacao_cadastral")),
        "data_abertura": normalizar_data_rfb(estabelecimento.get("data_inicio_atividade")),
        "uf": (estabelecimento.get("uf") or "").strip() or None,
        "cidade": municipios.get((estabelecimento.get("municipio") or "").strip()),
        "telefone": _montar_telefone(estabelecimento.get("ddd_1"), estabelecimento.get("telefone_1")),
        "email": (estabelecimento.get("correio_eletronico") or "").strip().lower() or None,
    }


def _montar_telefone(ddd: str | None, numero: str | None) -> str | None:
    ddd = (ddd or "").strip()
    numero = (numero or "").strip()
    if not numero:
        return None
    return f"{ddd}{numero}" if ddd else numero

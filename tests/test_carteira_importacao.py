import io

from openpyxl import Workbook

from app.api.carteira import (
    COLUNAS_EMPRESA,
    COLUNAS_NUMERO,
    COLUNAS_OBS,
    _chave_coluna,
    _ler_planilha,
    _valor,
)


def test_chave_coluna_remove_acento_e_maiuscula() -> None:
    assert _chave_coluna("Número") == "numero"
    assert _chave_coluna("Observações") == "observacoes"
    assert _chave_coluna(" Razão Social ") == "razaosocial"


def test_ler_csv_detecta_ponto_e_virgula_e_ignora_linhas_vazias() -> None:
    conteudo = "Numero;Empresa;Procurador\n900123456;Padaria X;Dr. Silva\n; ;\n909876543;Loja Y;\n"
    registros = _ler_planilha(conteudo.encode("utf-8"), "carteira.csv")
    assert len(registros) == 2
    assert _valor(registros[0], COLUNAS_NUMERO) == "900123456"
    assert _valor(registros[0], COLUNAS_EMPRESA) == "Padaria X"
    assert _valor(registros[1], COLUNAS_EMPRESA) == "Loja Y"


def test_ler_csv_com_virgula() -> None:
    registros = _ler_planilha(b"numero,observacoes\n900,teste\n", "x.csv")
    assert _valor(registros[0], COLUNAS_OBS) == "teste"


def test_ler_xlsx_com_cabecalho_acentuado_e_celula_numerica() -> None:
    wb = Workbook()
    planilha = wb.active
    planilha.append(["Número", "Empresa", "Observações"])
    planilha.append(["900111222", "Cafeteria Z", "marca principal"])
    planilha.append([909333444, "Bar W", None])
    buffer = io.BytesIO()
    wb.save(buffer)
    registros = _ler_planilha(buffer.getvalue(), "carteira.xlsx")
    assert len(registros) == 2
    assert _valor(registros[0], COLUNAS_NUMERO) == "900111222"
    assert _valor(registros[1], COLUNAS_NUMERO) == "909333444"
    assert _valor(registros[1], COLUNAS_EMPRESA) == "Bar W"


def test_planilha_so_com_cabecalho_retorna_vazio() -> None:
    assert _ler_planilha(b"numero,empresa\n", "x.csv") == []


def test_valor_ignora_colunas_desconhecidas() -> None:
    registro = {"numero": "900", "coluna_estranha": "lixo"}
    assert _valor(registro, COLUNAS_EMPRESA) is None

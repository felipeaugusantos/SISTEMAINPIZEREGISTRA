import io
import zipfile

from app.cli import importar_cnpj_rfb as modulo

# --- Fase 2 do Radar de Prospecção (03/09/2026) -- parsing dos arquivos RFB ---


def _zip_com_csv(nome_interno: str, linhas: list[str]) -> bytes:
    buffer = io.BytesIO()
    conteudo = "\n".join(linhas).encode("latin-1")
    with zipfile.ZipFile(buffer, "w") as arquivo:
        arquivo.writestr(nome_interno, conteudo)
    return buffer.getvalue()


def test_linhas_csv_do_zip_decodifica_latin1_e_separa_por_ponto_e_virgula() -> None:
    conteudo = _zip_com_csv("K3241.EMPRECSV", ["11222333;Loja Exemplo Ltda;2062;49;0,00;01;"])

    linhas = list(modulo._linhas_csv_do_zip(conteudo))

    assert linhas == [["11222333", "Loja Exemplo Ltda", "2062", "49", "0,00", "01", ""]]


def test_registros_mapeia_colunas_na_ordem_esperada() -> None:
    from app.rfb_cnpj import COLUNAS_EMPRESA

    conteudo = _zip_com_csv("K3241.EMPRECSV", ["11222333;Loja Exemplo Ltda;2062;49;0,00;01;"])

    registros = list(modulo._registros(conteudo, COLUNAS_EMPRESA))

    assert registros == [
        {
            "cnpj_basico": "11222333",
            "razao_social": "Loja Exemplo Ltda",
            "natureza_juridica": "2062",
            "qualificacao_responsavel": "49",
            "capital_social": "0,00",
            "porte_empresa": "01",
            "ente_federativo_responsavel": "",
        }
    ]


def test_registros_ignora_linha_curta_demais() -> None:
    from app.rfb_cnpj import COLUNAS_EMPRESA

    conteudo = _zip_com_csv("x.csv", ["11222333;Loja Exemplo"])

    assert list(modulo._registros(conteudo, COLUNAS_EMPRESA)) == []


def test_carregar_referencia_monta_dict_codigo_descricao(monkeypatch) -> None:
    conteudo = _zip_com_csv("F.K03200MUNICCSV", ["7107;SAO PAULO", "6001;RIO DE JANEIRO"])
    monkeypatch.setattr(modulo, "_baixar", lambda url: conteudo)

    resultado = modulo.carregar_referencia("2026-08", "Municipios")

    assert resultado == {"7107": "SAO PAULO", "6001": "RIO DE JANEIRO"}


def test_descobrir_periodo_mais_recente_pega_o_ultimo_da_listagem(monkeypatch) -> None:
    html = b"""
    <html><body>
    <a href="2026-06/">2026-06/</a>
    <a href="2026-07/">2026-07/</a>
    <a href="2026-08/">2026-08/</a>
    </body></html>
    """
    monkeypatch.setattr(modulo, "_baixar", lambda url: html)

    assert modulo.descobrir_periodo_mais_recente() == "2026-08"

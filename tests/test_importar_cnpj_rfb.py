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

    resultado = modulo.carregar_referencia("https://exemplo.com/webdav", "2026-08", "Municipios")

    assert resultado == {"7107": "SAO PAULO", "6001": "RIO DE JANEIRO"}


class _RespostaFalsa:
    def __init__(self, corpo: bytes) -> None:
        self._corpo = corpo

    def read(self) -> bytes:
        return self._corpo

    def __enter__(self) -> "_RespostaFalsa":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_descobrir_periodo_mais_recente_pega_o_ultimo_da_listagem_propfind(monkeypatch) -> None:
    # Resposta real de PROPFIND (WebDAV/Nextcloud) contra o compartilhamento
    # publico da RFB -- verificado manualmente em 03/09/2026.
    xml = b"""<?xml version="1.0"?>
    <d:multistatus xmlns:d="DAV:">
      <d:response><d:href>/public.php/webdav/</d:href></d:response>
      <d:response><d:href>/public.php/webdav/2026-06/</d:href></d:response>
      <d:response><d:href>/public.php/webdav/2026-07/</d:href></d:response>
      <d:response><d:href>/public.php/webdav/2026-08/</d:href></d:response>
      <d:response><d:href>/public.php/webdav/cnpj.tar.gz</d:href></d:response>
    </d:multistatus>"""
    monkeypatch.setattr(modulo, "urlopen", lambda *_args, **_kwargs: _RespostaFalsa(xml))

    assert modulo.descobrir_periodo_mais_recente("https://exemplo.com/webdav") == "2026-08"


def test_baixar_inclui_autenticacao_basic_quando_token_configurado(monkeypatch) -> None:
    from types import SimpleNamespace

    capturado = {}

    def _urlopen_falso(requisicao, timeout):  # noqa: ARG001 - assinatura espelha urlopen
        capturado["headers"] = dict(requisicao.header_items())
        return _RespostaFalsa(b"conteudo")

    monkeypatch.setattr(modulo, "urlopen", _urlopen_falso)
    monkeypatch.setattr(modulo, "get_settings", lambda: SimpleNamespace(rfb_cnpj_share_token="token-de-teste"))

    modulo._baixar("https://exemplo.com/webdav/2026-08/Municipios.zip")

    assert any(chave.lower() == "authorization" for chave in capturado["headers"])


def test_baixar_sem_token_nao_envia_autenticacao(monkeypatch) -> None:
    from types import SimpleNamespace

    capturado = {}

    def _urlopen_falso(requisicao, timeout):  # noqa: ARG001 - assinatura espelha urlopen
        capturado["headers"] = dict(requisicao.header_items())
        return _RespostaFalsa(b"conteudo")

    monkeypatch.setattr(modulo, "urlopen", _urlopen_falso)
    monkeypatch.setattr(modulo, "get_settings", lambda: SimpleNamespace(rfb_cnpj_share_token=""))

    modulo._baixar("https://exemplo.com/webdav/2026-08/Municipios.zip")

    assert not any(chave.lower() == "authorization" for chave in capturado["headers"])


# --- Callback de progresso (03/09/2026) -- disparo pela tela, superadmin ---


async def test_carregar_empresas_chama_progresso_uma_vez_por_arquivo(monkeypatch) -> None:
    conteudo = _zip_com_csv("K3241.EMPRECSV", ["11222333;Loja Exemplo Ltda;2062;49;0,00;01;"])
    monkeypatch.setattr(modulo, "_baixar", lambda url: conteudo)
    chamadas = []

    async def _progresso(etapa, processados, validos):
        chamadas.append((etapa, processados, validos))

    resultado = await modulo.carregar_empresas_por_cnpj_basico("https://exemplo.com/webdav", "2026-08", _progresso)

    assert resultado == {"11222333": ("01", "Loja Exemplo Ltda")}
    assert len(chamadas) == 10
    assert "Carregando empresas 1/10" in chamadas[0][0]
    assert "Carregando empresas 10/10" in chamadas[-1][0]


async def test_carregar_empresas_sem_callback_nao_quebra(monkeypatch) -> None:
    conteudo = _zip_com_csv("K3241.EMPRECSV", ["11222333;Loja Exemplo Ltda;2062;49;0,00;01;"])
    monkeypatch.setattr(modulo, "_baixar", lambda url: conteudo)

    resultado = await modulo.carregar_empresas_por_cnpj_basico("https://exemplo.com/webdav", "2026-08")

    assert resultado == {"11222333": ("01", "Loja Exemplo Ltda")}

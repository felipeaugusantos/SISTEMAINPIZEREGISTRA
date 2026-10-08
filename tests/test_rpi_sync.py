import ssl
import zipfile
from io import BytesIO
from pathlib import Path

from app.models import TipoProcesso
from app.rpi.sync import _INPI_CA_EXTRA, baixar_e_extrair_rpi, contexto_ssl_rpi, nome_zip


def test_nomes_dos_arquivos_oficiais() -> None:
    assert nome_zip(2818, TipoProcesso.MARCA) == "RM2818.zip"
    assert nome_zip(2818, TipoProcesso.PATENTE) == "P2818.zip"


def test_aceita_xml_de_marca_com_nome_numerico(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "RM2889.zip", "w") as pacote:
        pacote.writestr("2889.xml", '<revista numero="2889" data="19/05/2026"/>')

    extraido = baixar_e_extrair_rpi(2889, TipoProcesso.MARCA, tmp_path)

    assert extraido.name == "2889.xml"


def test_substitui_download_corrompido_de_forma_atomica(tmp_path: Path, monkeypatch) -> None:
    arquivo_zip = tmp_path / "RM2901.zip"
    arquivo_zip.write_bytes(b"download interrompido")

    conteudo = BytesIO()
    with zipfile.ZipFile(conteudo, "w") as pacote:
        pacote.writestr("RM2901.xml", '<revista numero="2901" data="11/08/2026"/>')
    payload = conteudo.getvalue()

    monkeypatch.setattr(
        "app.rpi.sync.urllib.request.urlopen",
        lambda *_args, **_kwargs: BytesIO(payload),
    )

    extraido = baixar_e_extrair_rpi(2901, TipoProcesso.MARCA, tmp_path)

    assert zipfile.is_zipfile(arquivo_zip)
    assert extraido.name == "RM2901.xml"
    assert not (tmp_path / "RM2901.zip.part").exists()


# --- Achado do usuário (22/09/2026): revistas.inpi.gov.br fica atrás de uma
# FortiGate que serve um certificado assinado por uma CA autoassinada da
# própria Fortinet -- confirmado (duas redes independentes viram o MESMO
# certificado) que é o servidor real, não uma interceptação de rede. A CA
# raiz é confiada só para este download, nunca desligando a verificação de
# SSL em geral. ---


def test_ca_extra_do_inpi_existe_e_e_um_certificado_valido() -> None:
    assert _INPI_CA_EXTRA.is_file()
    conteudo = _INPI_CA_EXTRA.read_text()
    assert conteudo.startswith("-----BEGIN CERTIFICATE-----")
    assert conteudo.strip().endswith("-----END CERTIFICATE-----")


def test_contexto_ssl_rpi_carrega_a_ca_extra_sem_erro() -> None:
    contexto = contexto_ssl_rpi()
    assert isinstance(contexto, ssl.SSLContext)
    # A CA raiz da FortiGate foi somada ao trust store padrão do sistema
    # (load_verify_locations soma, não substitui) -- confere que o
    # carregamento realmente aconteceu contando os certificados confiáveis.
    assert contexto.cert_store_stats()["x509_ca"] >= 1


def test_baixar_zip_passa_o_contexto_ssl_customizado_para_urlopen(tmp_path: Path, monkeypatch) -> None:
    conteudo = BytesIO()
    with zipfile.ZipFile(conteudo, "w") as pacote:
        pacote.writestr("RM3000.xml", '<revista numero="3000" data="22/09/2026"/>')
    payload = conteudo.getvalue()

    chamadas: list[dict] = []

    def _urlopen_espiao(*_args: object, **kwargs: object) -> BytesIO:
        chamadas.append(kwargs)
        return BytesIO(payload)

    monkeypatch.setattr("app.rpi.sync.urllib.request.urlopen", _urlopen_espiao)

    baixar_e_extrair_rpi(3000, TipoProcesso.MARCA, tmp_path)

    assert len(chamadas) == 1
    assert isinstance(chamadas[0]["context"], ssl.SSLContext)

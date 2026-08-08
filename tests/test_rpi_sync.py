import zipfile
from io import BytesIO
from pathlib import Path

from app.models import TipoProcesso
from app.rpi.sync import baixar_e_extrair_rpi, nome_zip


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

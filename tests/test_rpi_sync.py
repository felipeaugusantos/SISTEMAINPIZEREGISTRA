import zipfile
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

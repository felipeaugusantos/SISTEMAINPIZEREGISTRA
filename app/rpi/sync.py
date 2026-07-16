import shutil
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from app.models import TipoProcesso

BASE_RPI = "https://revistas.inpi.gov.br/txt"


def nome_zip(numero_rpi: int, tipo: TipoProcesso) -> str:
    prefixo = "RM" if tipo is TipoProcesso.MARCA else "P"
    return f"{prefixo}{numero_rpi}.zip"


def baixar_e_extrair_rpi(
    numero_rpi: int,
    tipo: TipoProcesso,
    diretorio: Path,
    progresso: Callable[[str], None] | None = None,
) -> Path:
    diretorio.mkdir(parents=True, exist_ok=True)
    arquivo_zip = diretorio / nome_zip(numero_rpi, tipo)

    if not arquivo_zip.is_file():
        url = f"{BASE_RPI}/{arquivo_zip.name}"
        if progresso:
            progresso(f"Baixando {url}")
        requisicao = urllib.request.Request(url, headers={"User-Agent": "INPI-API/0.1"})
        with urllib.request.urlopen(requisicao, timeout=120) as resposta:
            with arquivo_zip.open("wb") as destino:
                shutil.copyfileobj(resposta, destino)

    with zipfile.ZipFile(arquivo_zip) as pacote:
        arquivos_xml = [nome for nome in pacote.namelist() if nome.lower().endswith(".xml")]
        if tipo is TipoProcesso.MARCA:
            candidatos = [
                nome for nome in arquivos_xml if Path(nome).name.upper().startswith("RM")
            ]
            if not candidatos:
                candidatos = [
                    nome
                    for nome in arquivos_xml
                    if not Path(nome).name.upper().startswith("PATENTE_")
                ]
        else:
            candidatos = [
                nome for nome in arquivos_xml if Path(nome).name.upper().startswith("PATENTE_")
            ]
        if not candidatos:
            raise ValueError(f"XML de {tipo.value} não encontrado em {arquivo_zip.name}")

        membro = candidatos[0]
        destino_xml = diretorio / Path(membro).name
        if not destino_xml.is_file():
            with pacote.open(membro) as origem, destino_xml.open("wb") as destino:
                shutil.copyfileobj(origem, destino)
    return destino_xml

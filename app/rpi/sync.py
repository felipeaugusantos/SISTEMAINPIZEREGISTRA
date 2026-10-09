import shutil
import ssl
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from app.models import TipoProcesso

BASE_RPI = "https://revistas.inpi.gov.br/txt"
# Códigos HTTP tratados como indisponibilidade transitória do portal do INPI.
_HTTP_TRANSITORIOS = frozenset({500, 502, 503, 504})
_TENTATIVAS_DOWNLOAD = 5

# Achado do usuário (22/09/2026): revistas.inpi.gov.br fica atrás de uma
# FortiGate (WAF/proxy) que serve um certificado assinado por uma CA
# autoassinada da própria Fortinet, nunca substituída por um certificado
# público válido -- não é interceptação de rede (confirmado batendo o
# mesmo certificado a partir de duas redes independentes, VPS e uma
# rede doméstica; se fosse MITM local, cada rede veria um certificado
# diferente). Como é comprovadamente o servidor real de destino, e não
# um terceiro desconhecido, confiamos nesta CA específica só para este
# download -- nunca desligamos a verificação de SSL globalmente nem em
# nenhum outro lugar do sistema. Pinada a CA raiz (validade até
# 2027-07-08), não o certificado-folha (expira em semanas e é renovado
# pela mesma FortiGate), para sobreviver às renovações.
_INPI_CA_EXTRA = Path(__file__).parent / "inpi_fortinet_ca.pem"


def contexto_ssl_rpi() -> ssl.SSLContext:
    contexto = ssl.create_default_context()
    contexto.load_verify_locations(cafile=str(_INPI_CA_EXTRA))
    return contexto


def nome_zip(numero_rpi: int, tipo: TipoProcesso) -> str:
    prefixo = "RM" if tipo is TipoProcesso.MARCA else "P"
    return f"{prefixo}{numero_rpi}.zip"


def _baixar_zip_atomico(url: str, arquivo_zip: Path) -> None:
    arquivo_temporario = arquivo_zip.with_suffix(f"{arquivo_zip.suffix}.part")
    requisicao = urllib.request.Request(url, headers={"User-Agent": "INPI-API/0.1"})
    contexto_ssl = contexto_ssl_rpi()
    try:
        for tentativa in range(_TENTATIVAS_DOWNLOAD):
            try:
                with urllib.request.urlopen(requisicao, timeout=120, context=contexto_ssl) as resposta:
                    with arquivo_temporario.open("wb") as destino:
                        shutil.copyfileobj(resposta, destino)
                if not zipfile.is_zipfile(arquivo_temporario):
                    # O portal do INPI às vezes responde 200 com HTML de erro no lugar do
                    # ZIP; tratamos como transitório e tentamos de novo.
                    raise zipfile.BadZipFile(f"Download inválido recebido de {url}")
                arquivo_temporario.replace(arquivo_zip)
                return
            except (urllib.error.URLError, TimeoutError, zipfile.BadZipFile) as exc:
                # 4xx (ex.: 404 = edição não publicada) falha na hora; 5xx, rede e
                # conteúdo inválido (BadZipFile) são transitórios e tentam de novo.
                transitorio = not isinstance(exc, urllib.error.HTTPError) or exc.code in _HTTP_TRANSITORIOS
                if transitorio and tentativa < _TENTATIVAS_DOWNLOAD - 1:
                    time.sleep(3 * (tentativa + 1))  # backoff: 3, 6, 9, 12s
                    continue
                raise
    finally:
        arquivo_temporario.unlink(missing_ok=True)


def baixar_e_extrair_rpi(
    numero_rpi: int,
    tipo: TipoProcesso,
    diretorio: Path,
    progresso: Callable[[str], None] | None = None,
) -> Path:
    diretorio.mkdir(parents=True, exist_ok=True)
    arquivo_zip = diretorio / nome_zip(numero_rpi, tipo)

    if not arquivo_zip.is_file() or not zipfile.is_zipfile(arquivo_zip):
        url = f"{BASE_RPI}/{arquivo_zip.name}"
        if progresso:
            progresso(f"Baixando {url}")
        _baixar_zip_atomico(url, arquivo_zip)

    with zipfile.ZipFile(arquivo_zip) as pacote:
        arquivos_xml = [nome for nome in pacote.namelist() if nome.lower().endswith(".xml")]
        if tipo is TipoProcesso.MARCA:
            candidatos = [nome for nome in arquivos_xml if Path(nome).name.upper().startswith("RM")]
            if not candidatos:
                candidatos = [nome for nome in arquivos_xml if not Path(nome).name.upper().startswith("PATENTE_")]
        else:
            candidatos = [nome for nome in arquivos_xml if Path(nome).name.upper().startswith("PATENTE_")]
        if not candidatos:
            raise ValueError(f"XML de {tipo.value} não encontrado em {arquivo_zip.name}")

        membro = candidatos[0]
        destino_xml = diretorio / Path(membro).name
        if not destino_xml.is_file():
            with pacote.open(membro) as origem, destino_xml.open("wb") as destino:
                shutil.copyfileobj(origem, destino)
    return destino_xml

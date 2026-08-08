import re
import urllib.request

PAGINA_RPI_OFICIAL = "https://revistas.inpi.gov.br/rpi/"
PADRAO_NUMERO_RPI = re.compile(r"<td[^>]*>\s*(\d{4})\s*</td>", re.IGNORECASE)


def extrair_ultima_rpi(html: str) -> int:
    numeros = {int(numero) for numero in PADRAO_NUMERO_RPI.findall(html)}
    if not numeros:
        raise ValueError("Nenhum número de RPI foi encontrado na página oficial")
    return max(numeros)


def consultar_ultima_rpi(url: str = PAGINA_RPI_OFICIAL) -> int:
    requisicao = urllib.request.Request(url, headers={"User-Agent": "INPI-API/0.1"})
    with urllib.request.urlopen(requisicao, timeout=60) as resposta:
        html = resposta.read().decode("utf-8", errors="replace")
    return extrair_ultima_rpi(html)

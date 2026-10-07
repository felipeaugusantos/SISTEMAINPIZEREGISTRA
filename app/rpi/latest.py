import re
import time
import urllib.error
import urllib.request

PAGINA_RPI_OFICIAL = "https://revistas.inpi.gov.br/rpi/"
PADRAO_NUMERO_RPI = re.compile(r"<td[^>]*>\s*(\d{4})\s*</td>", re.IGNORECASE)

# Mesmo tratamento de indisponibilidade transitória do portal do INPI usado no
# download dos ZIPs (app/rpi/sync.py) -- achado de 07/10/2026: essa consulta
# não tinha retry e derrubava a checagem inteira numa instabilidade pontual de
# segundos do portal, mesmo com a importação da edição já concluída.
_HTTP_TRANSITORIOS = frozenset({500, 502, 503, 504})
_TENTATIVAS_CONSULTA = 5


def extrair_ultima_rpi(html: str) -> int:
    numeros = {int(numero) for numero in PADRAO_NUMERO_RPI.findall(html)}
    if not numeros:
        raise ValueError("Nenhum número de RPI foi encontrado na página oficial")
    return max(numeros)


def consultar_ultima_rpi(url: str = PAGINA_RPI_OFICIAL) -> int:
    requisicao = urllib.request.Request(url, headers={"User-Agent": "INPI-API/0.1"})
    for tentativa in range(_TENTATIVAS_CONSULTA):
        try:
            with urllib.request.urlopen(requisicao, timeout=60) as resposta:
                html = resposta.read().decode("utf-8", errors="replace")
            return extrair_ultima_rpi(html)
        except (urllib.error.URLError, TimeoutError) as exc:
            transitorio = not isinstance(exc, urllib.error.HTTPError) or exc.code in _HTTP_TRANSITORIOS
            if transitorio and tentativa < _TENTATIVAS_CONSULTA - 1:
                time.sleep(3 * (tentativa + 1))  # backoff: 3, 6, 9, 12s
                continue
            raise
    raise AssertionError("inalcançável")

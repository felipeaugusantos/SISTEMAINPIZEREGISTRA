import asyncio

import httpx
import pytest

from app.inpi_titular_live import buscar_titularidade_inpi_ao_vivo, limpar_estado_titularidade_inpi
from app.settings import get_settings

# --- Achado de 08/09/2026: busca ao vivo por CNPJ no site do INPI (pePI),
# fallback best-effort à checagem por nome na base local (ver
# app.prospeccao_triagem.buscar_titularidade_ampla). Confirmado em teste
# manual: o pePI é um sistema legado instável (502/timeout repetidos) --
# por isso qualquer erro aqui devolve None em silêncio, nunca levanta.

CNPJ_VALIDO = "07526557000100"


@pytest.fixture(autouse=True)
def _limpar_cache_e_limite() -> None:
    limpar_estado_titularidade_inpi()
    yield
    limpar_estado_titularidade_inpi()

_HTML_TRES_RESULTADOS = """
<b>RESULTADO DA PESQUISA</b>
Foram encontrados <b>3</b> registros que satisfazem à pesquisa.
<a href="/pePI/servlet/MarcasServletController?Action=searchMarca&amp;tipoPesquisa=BY_CNPJ_NOME&amp;pos=0" class="normal">
    AMBEV S.A
</a>
<a href="/pePI/servlet/MarcasServletController?Action=searchMarca&amp;tipoPesquisa=BY_CNPJ_NOME&amp;pos=1" class="normal">
    AMBEV S.A.
</a>
"""

_HTML_ZERO_RESULTADOS = """
<b>RESULTADO DA PESQUISA</b>
Foram encontrados <b>0</b> registros que satisfazem à pesquisa.
"""


def _cliente_mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://busca.inpi.gov.br/pePI"
    )


async def test_flag_desligada_por_padrao_devolve_none_sem_chamar_rede() -> None:
    settings = get_settings()
    assert settings.prospeccao_titularidade_inpi_ao_vivo_enabled is False  # padrão, sem precisar mexer na flag

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria fazer nenhuma chamada com a flag desligada")

    resultado = await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=_cliente_mock(handler))
    assert resultado is None


async def test_cnpj_invalido_devolve_none_sem_chamar_rede() -> None:
    settings = get_settings()
    original = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria fazer nenhuma chamada com CNPJ inválido")

    try:
        resultado = await buscar_titularidade_inpi_ao_vivo("123", cliente=_cliente_mock(handler))
    finally:
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original
    assert resultado is None


async def test_acha_titular_com_resultados() -> None:
    settings = get_settings()
    original = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True

    def handler(request: httpx.Request) -> httpx.Response:
        if "LoginController" in str(request.url):
            return httpx.Response(200, text="<html>login ok</html>")
        return httpx.Response(200, text=_HTML_TRES_RESULTADOS)

    try:
        resultado = await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=_cliente_mock(handler))
    finally:
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original
    assert resultado == "AMBEV S.A"


async def test_zero_registros_devolve_none() -> None:
    settings = get_settings()
    original = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True

    def handler(request: httpx.Request) -> httpx.Response:
        if "LoginController" in str(request.url):
            return httpx.Response(200, text="<html>login ok</html>")
        return httpx.Response(200, text=_HTML_ZERO_RESULTADOS)

    try:
        resultado = await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=_cliente_mock(handler))
    finally:
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original
    assert resultado is None


async def test_erro_502_devolve_none_sem_levantar() -> None:
    # Achado em teste manual desta sessão: o pePI devolveu 502 repetidamente
    # em consultas reais -- o comportamento aqui precisa ser silencioso.
    settings = get_settings()
    original = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True

    def handler(request: httpx.Request) -> httpx.Response:
        if "LoginController" in str(request.url):
            return httpx.Response(200, text="<html>login ok</html>")
        return httpx.Response(502, text="Bad Gateway")

    try:
        resultado = await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=_cliente_mock(handler))
    finally:
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original
    assert resultado is None


async def test_timeout_devolve_none_sem_levantar() -> None:
    settings = get_settings()
    original = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout simulado", request=request)

    try:
        resultado = await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=_cliente_mock(handler))
    finally:
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original
    assert resultado is None


async def test_html_inesperado_sem_padrao_reconhecido_devolve_none() -> None:
    settings = get_settings()
    original = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>página completamente diferente do esperado</html>")

    try:
        resultado = await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=_cliente_mock(handler))
    finally:
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original
    assert resultado is None


async def test_cache_evitar_repetir_consulta_ao_inpi_para_o_mesmo_cnpj() -> None:
    settings = get_settings()
    original = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True
    chamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        chamadas += 1
        if "LoginController" in str(request.url):
            return httpx.Response(200, text="<html>login ok</html>")
        return httpx.Response(200, text=_HTML_TRES_RESULTADOS)

    cliente = _cliente_mock(handler)
    try:
        primeiro = await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=cliente)
        segundo = await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=cliente)
    finally:
        await cliente.aclose()
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original

    assert primeiro == segundo == "AMBEV S.A"
    assert chamadas == 2  # login + pesquisa apenas na primeira consulta


async def test_consultas_simultaneas_do_mesmo_cnpj_compartilham_uma_chamada() -> None:
    settings = get_settings()
    original = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True
    chamadas = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        chamadas += 1
        await asyncio.sleep(0.01)
        if "LoginController" in str(request.url):
            return httpx.Response(200, text="<html>login ok</html>")
        return httpx.Response(200, text=_HTML_TRES_RESULTADOS)

    cliente = _cliente_mock(handler)
    try:
        resultados = await asyncio.gather(
            buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=cliente),
            buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=cliente),
        )
    finally:
        await cliente.aclose()
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original

    assert resultados == ["AMBEV S.A", "AMBEV S.A"]
    assert chamadas == 2


async def test_cache_negativo_evitar_repetir_consulta_sem_resultados() -> None:
    settings = get_settings()
    original = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True
    chamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        chamadas += 1
        if "LoginController" in str(request.url):
            return httpx.Response(200, text="<html>login ok</html>")
        return httpx.Response(200, text=_HTML_ZERO_RESULTADOS)

    cliente = _cliente_mock(handler)
    try:
        assert await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=cliente) is None
        assert await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=cliente) is None
    finally:
        await cliente.aclose()
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original

    assert chamadas == 2


async def test_limite_global_impede_nova_consulta_externa_na_mesma_janela() -> None:
    settings = get_settings()
    original_enabled = settings.prospeccao_titularidade_inpi_ao_vivo_enabled
    original_limite = settings.prospeccao_titularidade_inpi_max_chamadas_minuto
    settings.prospeccao_titularidade_inpi_ao_vivo_enabled = True
    settings.prospeccao_titularidade_inpi_max_chamadas_minuto = 1
    chamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        chamadas += 1
        if "LoginController" in str(request.url):
            return httpx.Response(200, text="<html>login ok</html>")
        return httpx.Response(200, text=_HTML_ZERO_RESULTADOS)

    cliente = _cliente_mock(handler)
    try:
        await buscar_titularidade_inpi_ao_vivo(CNPJ_VALIDO, cliente=cliente)
        bloqueado = await buscar_titularidade_inpi_ao_vivo("11222333000181", cliente=cliente)
    finally:
        await cliente.aclose()
        settings.prospeccao_titularidade_inpi_ao_vivo_enabled = original_enabled
        settings.prospeccao_titularidade_inpi_max_chamadas_minuto = original_limite

    assert bloqueado is None
    assert chamadas == 2

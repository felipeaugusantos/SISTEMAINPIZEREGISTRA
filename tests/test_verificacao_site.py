import httpx

from app.verificacao_site import normalizar_url, verificar_site

# --- Fase 3 do Radar de Prospecção (03/09/2026) -- verificação de site -----


def test_normalizar_url_adiciona_https_quando_falta_esquema() -> None:
    assert normalizar_url("exemplo.com.br") == "https://exemplo.com.br"


def test_normalizar_url_preserva_esquema_existente() -> None:
    assert normalizar_url("http://exemplo.com.br") == "http://exemplo.com.br"
    assert normalizar_url("https://exemplo.com.br") == "https://exemplo.com.br"


def test_normalizar_url_vazia_retorna_none() -> None:
    assert normalizar_url("") is None
    assert normalizar_url(None) is None
    assert normalizar_url("   ") is None


async def test_verificar_site_sem_site_devolve_ativo_none() -> None:
    resultado = await verificar_site(None)
    assert resultado == {"ativo": None, "status_code": None, "url_final": None, "erro": None}


async def test_verificar_site_ativo_com_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("exemplo.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is True
    assert resultado["status_code"] == 200
    assert resultado["erro"] is None


async def test_verificar_site_404_marca_inativo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("exemplo.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["status_code"] == 404


async def test_verificar_site_repete_como_get_quando_head_nao_e_aceito() -> None:
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("exemplo.com.br", cliente=cliente)
    await cliente.aclose()

    assert chamadas == ["HEAD", "GET"]
    assert resultado["ativo"] is True
    assert resultado["status_code"] == 200


async def test_verificar_site_erro_de_conexao_marca_inativo_com_erro() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexão recusada", request=request)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("site-fora-do-ar.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["status_code"] is None
    assert resultado["erro"] == "ConnectError"

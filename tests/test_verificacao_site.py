import socket

import httpx
import pytest

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


def _resolver_para_ip_publico(monkeypatch: pytest.MonkeyPatch, ip: str = "93.184.216.34") -> None:
    """Achado FASE5-1 da auditoria (04/09/2026): verificar_site agora resolve
    o host antes de conectar (proteção de SSRF) -- os testes precisam
    controlar essa resolução em vez de depender de DNS real."""
    monkeypatch.setattr(
        "app.verificacao_site.socket.getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))],
    )


async def test_verificar_site_ativo_com_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolver_para_ip_publico(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("exemplo.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is True
    assert resultado["status_code"] == 200
    assert resultado["erro"] is None


async def test_verificar_site_404_marca_inativo(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolver_para_ip_publico(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("exemplo.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["status_code"] == 404


async def test_verificar_site_repete_como_get_quando_head_nao_e_aceito(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolver_para_ip_publico(monkeypatch)
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


async def test_verificar_site_erro_de_conexao_marca_inativo_com_erro(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolver_para_ip_publico(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexão recusada", request=request)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("site-fora-do-ar.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["status_code"] is None
    assert resultado["erro"] == "ConnectError"


# --- Achado FASE5-1 da auditoria (04/09/2026): SSRF -- a versão anterior
# fazia a requisição para qualquer `site` sem nenhuma validação. `site` vem
# de importação em massa (CSV) e de campanhas de coleta -- dado que não é
# digitado por quem roda a verificação. ---


def _handler_nunca_deveria_ser_chamado(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"requisição não deveria ter sido feita para {request.url}")


async def test_verificar_site_bloqueia_localhost() -> None:
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(_handler_nunca_deveria_ser_chamado))
    resultado = await verificar_site("http://localhost:8000/admin", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["erro"] == "DestinoBloqueado"


@pytest.mark.parametrize(
    "ip_privado",
    ["127.0.0.1", "10.0.0.5", "172.20.0.3", "192.168.1.1", "169.254.169.254"],
)
async def test_verificar_site_bloqueia_ips_privados_e_metadata_cloud(
    monkeypatch: pytest.MonkeyPatch, ip_privado: str
) -> None:
    _resolver_para_ip_publico(monkeypatch, ip=ip_privado)
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(_handler_nunca_deveria_ser_chamado))

    resultado = await verificar_site("http://site-disfarcado.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["erro"] == "DestinoBloqueado"


async def test_verificar_site_bloqueia_esquema_fora_de_http_https() -> None:
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(_handler_nunca_deveria_ser_chamado))
    resultado = await verificar_site("file:///etc/passwd", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["erro"] == "DestinoBloqueado"


async def test_verificar_site_bloqueia_dns_que_nao_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falha_resolucao(host: str, port: object) -> list:
        raise socket.gaierror("nome não resolvido")

    monkeypatch.setattr("app.verificacao_site.socket.getaddrinfo", _falha_resolucao)
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(_handler_nunca_deveria_ser_chamado))

    resultado = await verificar_site("http://dominio-inexistente.invalido", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["erro"] == "DestinoBloqueado"


async def test_verificar_site_bloqueia_redirecionamento_para_ip_privado(monkeypatch: pytest.MonkeyPatch) -> None:
    """O primeiro salto resolve pra um IP público (passa na validação), mas
    redireciona pra um destino privado -- precisa ser bloqueado no segundo
    salto, não só no primeiro."""

    def resolver(host: str, _port: object):
        ip = "93.184.216.34" if host == "site-publico.com.br" else "10.0.0.9"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr("app.verificacao_site.socket.getaddrinfo", resolver)

    def handler(request: httpx.Request) -> httpx.Response:
        if "site-publico" in str(request.url):
            return httpx.Response(302, headers={"location": "http://site-interno.local/painel"})
        raise AssertionError(f"não deveria ter seguido o redirecionamento até {request.url}")

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("http://site-publico.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["erro"] == "DestinoBloqueado"


async def test_verificar_site_limita_quantidade_de_redirecionamentos(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolver_para_ip_publico(monkeypatch)
    contador = {"chamadas": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        contador["chamadas"] += 1
        return httpx.Response(302, headers={"location": "https://exemplo.com.br/proxima"})

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("https://exemplo.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["erro"] == "MuitosRedirecionamentos"
    # 1 tentativa inicial + MAX_REDIRECIONAMENTOS (3) = 4 chamadas HEAD, nunca mais que isso.
    assert contador["chamadas"] == 4

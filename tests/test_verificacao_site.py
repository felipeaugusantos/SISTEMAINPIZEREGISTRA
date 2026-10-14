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
        if request.headers["host"] == "site-publico.com.br":
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


# --- Auditoria externa de 13/10/2026, A09: DNS rebinding. Validar o DNS e deixar o client resolver de novo
# ao conectar deixava uma janela. Agora a conexão é feita direto no IP já validado. ---


async def test_a09_conecta_no_ip_validado_e_nunca_resolve_de_novo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula o rebinding: a 1ª resolução devolve IP público e qualquer outra devolveria um IP interno."""
    resolucoes: list[str] = []

    def resolver(host: str, _porta: object):
        resolucoes.append(host)
        ip = "93.184.216.34" if len(resolucoes) == 1 else "10.0.0.9"  # só a 1ª é pública
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr("app.verificacao_site.socket.getaddrinfo", resolver)
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("https://exemplo.com.br/pagina?x=1", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is True
    assert resolucoes == ["exemplo.com.br"]  # resolvido UMA vez; o client não recebeu o nome para resolver
    requisicao = vistas[0]
    assert requisicao.url.host == "93.184.216.34"  # conexão direto no IP validado
    assert requisicao.url.path == "/pagina" and requisicao.url.query == b"x=1"
    assert requisicao.headers["host"] == "exemplo.com.br"  # virtual host preservado
    assert requisicao.extensions["sni_hostname"] == "exemplo.com.br"  # SNI/certificado pelo nome original
    assert resultado["url_final"] == "https://exemplo.com.br/pagina?x=1"


async def test_a09_porta_explicita_vai_no_ip_e_no_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolver_para_ip_publico(monkeypatch)
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await verificar_site("http://exemplo.com.br:8080/", cliente=cliente)
    await cliente.aclose()

    assert vistas[0].url.host == "93.184.216.34" and vistas[0].url.port == 8080
    assert vistas[0].headers["host"] == "exemplo.com.br:8080"


async def test_a09_credenciais_da_url_nao_sao_enviadas(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolver_para_ip_publico(monkeypatch)
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await verificar_site("https://usuario:senha@exemplo.com.br/", cliente=cliente)
    await cliente.aclose()

    assert "authorization" not in vistas[0].headers
    assert vistas[0].url.userinfo == b""


async def test_a09_tenta_os_outros_ips_validados_quando_a_conexao_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    # IPv6 primeiro (sem rota), IPv4 depois: sem o fallback a verificação daria falso "fora do ar".
    monkeypatch.setattr(
        "app.verificacao_site.socket.getaddrinfo",
        lambda host, port: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ],
    )
    tentados: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tentados.append(request.url.host)
        if ":" in request.url.host:
            raise httpx.ConnectError("sem rota IPv6", request=request)
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("exemplo.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is True
    assert tentados == ["2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34"]


async def test_a09_um_ip_privado_entre_os_resolvidos_bloqueia_tudo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.verificacao_site.socket.getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.10", 0)),
        ],
    )
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(_handler_nunca_deveria_ser_chamado))
    resultado = await verificar_site("exemplo.com.br", cliente=cliente)
    await cliente.aclose()

    assert resultado["erro"] == "DestinoBloqueado"


@pytest.mark.parametrize(
    "site",
    [
        "http://127.0.0.1/",
        "http://127.1/",  # forma abreviada resolvida pelo getaddrinfo
        "http://2130706433/",  # 127.0.0.1 em decimal
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # IPv4 mapeado em IPv6
        "http://[::ffff:10.0.0.5]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
    ],
)
async def test_a09_bloqueia_ip_literal_e_formas_alternativas(site: str) -> None:
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(_handler_nunca_deveria_ser_chamado))
    resultado = await verificar_site(site, cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is False
    assert resultado["erro"] == "DestinoBloqueado"


async def test_a09_ip_literal_publico_conecta_sem_resolver_nome(monkeypatch: pytest.MonkeyPatch) -> None:
    def nao_deveria_resolver(host: str, _porta: object):
        raise AssertionError("IP literal não precisa de DNS")

    monkeypatch.setattr("app.verificacao_site.socket.getaddrinfo", nao_deveria_resolver)
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("http://93.184.216.34/", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is True
    assert vistas[0].url.host == "93.184.216.34"
    assert vistas[0].headers["host"] == "93.184.216.34"


# --- Revisão do Codex no PR #191 ---


async def test_a09_dominio_com_acento_vira_punycode_no_host_e_no_sni(monkeypatch: pytest.MonkeyPatch) -> None:
    ascii_esperado = "münich.example".encode("idna").decode("ascii")
    resolvidos: list[str] = []

    def resolver(host: str, _porta: object):
        resolvidos.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("app.verificacao_site.socket.getaddrinfo", resolver)
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("https://münich.example/", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is True  # não levanta UnicodeEncodeError ao montar o Host
    assert resolvidos == [ascii_esperado]
    assert vistas[0].headers["host"] == ascii_esperado
    assert vistas[0].extensions["sni_hostname"] == ascii_esperado


async def test_a09_nome_invalido_nao_escapa_como_excecao() -> None:
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(_handler_nunca_deveria_ser_chamado))
    longo = await verificar_site("https://" + "a" * 70 + ".com", cliente=cliente)  # rótulo IDNA > 63
    malformada = await verificar_site("http://[abc/", cliente=cliente)  # colchete de IPv6 sem fechar
    await cliente.aclose()

    assert longo["ativo"] is False and longo["erro"] == "DestinoBloqueado"
    assert malformada["ativo"] is False and malformada["erro"] == "UrlInvalida"


async def test_a09_cada_requisicao_abre_conexao_propria_mesmo_com_hosts_no_mesmo_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Dois nomes no mesmo IP/porta teriam a mesma origem no pool do httpcore e a segunda requisição
    # reaproveitaria a conexão TLS da primeira (certificado do 2º nome nunca verificado).
    _resolver_para_ip_publico(monkeypatch)
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        if request.headers["host"] == "primeiro.com.br":
            return httpx.Response(302, headers={"location": "https://segundo.com.br/"})
        return httpx.Response(200)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resultado = await verificar_site("https://primeiro.com.br/", cliente=cliente)
    await cliente.aclose()

    assert resultado["ativo"] is True
    assert [r.headers["host"] for r in vistas] == ["primeiro.com.br", "segundo.com.br"]
    assert [r.extensions["sni_hostname"] for r in vistas] == ["primeiro.com.br", "segundo.com.br"]
    assert all(r.url.host == "93.184.216.34" for r in vistas)  # mesmo IP, mesma origem no pool
    assert all(r.headers["connection"] == "close" for r in vistas)

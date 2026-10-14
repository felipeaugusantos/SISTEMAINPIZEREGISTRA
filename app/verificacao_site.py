"""Verificação de presença digital -- Fase 3 do Radar de Prospecção
(03/09/2026, docs/arquitetura-radar-prospeccao-2026-09-03.md).

Fonte escolhida pelo usuário: só checar se o site do prospect responde, sem
provedor pago. Não descobre nada que o prospect não trouxe -- sem redes
sociais, sem site quando o campo `site` está vazio (ativo=None nesse caso,
que é diferente de "site fora do ar").

Achado FASE5-1 da auditoria (04/09/2026): a versão anterior fazia a
requisição HTTP direto para o valor de `site` sem nenhuma validação --
localhost, IPs privados (10.x/172.16-31.x/192.168.x), o endereço de
metadata de nuvem (169.254.169.254, coberto por is_link_local),
redirecionamento para rede interna e esquemas fora de http/https passavam
todos sem checagem. Como `site` vem de importação em massa (CSV) e de
campanhas de coleta -- dado que não é digitado por quem vai rodar a
verificação -- isso era um SSRF real: um `site` malicioso fazia a própria
API fazer a requisição por dentro da rede.

Mitigação: resolve o host antes de conectar e bloqueia se qualquer IP
resolvido for privado/reservado/loopback/link-local/multicast; segue
redirecionamentos manualmente (sem follow_redirects automático), validando
CADA salto do mesmo jeito, com limite de saltos; limita o tamanho do corpo
lido no fallback GET.

DNS rebinding (auditoria externa de 13/10/2026, A09): validar o DNS e deixar o
client HTTP resolver DE NOVO ao conectar deixava uma janela em que o host
respondia IP público na validação e IP privado na conexão. Agora o host é
resolvido UMA vez por salto, todos os IPs resolvidos são validados e a conexão
é feita direto no IP validado (a URL vai com o IP; o cabeçalho Host e o SNI
continuam sendo o nome original, então virtual hosts e a verificação do
certificado TLS seguem funcionando). Os redirecionamentos continuam validados
salto a salto. Fora do código, o reforço recomendado é uma política de saída
de rede que bloqueie destinos internos e serviços de metadata.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

TIMEOUT_SEGUNDOS = 10
USER_AGENT = "INPI-API/0.1 (radar de prospeccao -- verificacao de site)"
ESQUEMAS_PERMITIDOS = frozenset({"http", "https"})
HOSTS_BLOQUEADOS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
MAX_REDIRECIONAMENTOS = 3
TAMANHO_MAXIMO_RESPOSTA_BYTES = 1_000_000  # só precisamos saber se responde, não o conteúdo


class DestinoBloqueadoError(Exception):
    """URL aponta para um destino interno/privado, ou usa esquema não permitido -- bloqueada para evitar SSRF."""


def normalizar_url(site: str | None) -> str | None:
    site = (site or "").strip()
    if not site:
        return None
    if not site.lower().startswith(("http://", "https://")):
        site = f"https://{site}"
    return site


def _ip_e_privado_ou_reservado(ip_str: str) -> bool:
    endereco = ipaddress.ip_address(ip_str)
    if endereco.version == 6 and endereco.ipv4_mapped is not None:
        endereco = endereco.ipv4_mapped  # ::ffff:127.0.0.1 vale como 127.0.0.1
    return (
        endereco.is_private
        or endereco.is_loopback
        or endereco.is_link_local  # cobre 169.254.169.254 (metadata AWS/GCP/Azure)
        or endereco.is_multicast
        or endereco.is_reserved
        or endereco.is_unspecified
    )


MAX_IPS_POR_DESTINO = 4


def _bracket(ip_ou_host: str) -> str:
    return f"[{ip_ou_host}]" if ":" in ip_ou_host else ip_ou_host


def _host_ascii(host: str) -> str:
    """Nome em ASCII (IDNA/punycode): o Host e o SNI precisam ser ASCII e o getaddrinfo resolve o mesmo nome."""
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DestinoBloqueadoError(f"host inválido: {host}") from exc


def _host_literal_ip(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


async def _resolver_e_validar(url: str) -> tuple[str, int, list[str]]:
    """Resolve o host UMA vez e valida todos os IPs; devolve (host, porta, ips validados).

    Levanta DestinoBloqueadoError se a URL usar esquema fora de http/https, tiver host
    vazio/bloqueado, não resolver, ou se QUALQUER IP resolvido for privado/reservado. A conexão
    deve usar os IPs devolvidos -- nunca resolver o nome de novo (DNS rebinding, A09).
    """
    partes = urlparse(url)
    if partes.scheme not in ESQUEMAS_PERMITIDOS:
        raise DestinoBloqueadoError(f"esquema não permitido: {partes.scheme or '(vazio)'}")
    host = partes.hostname
    if not host:
        raise DestinoBloqueadoError("URL sem host")
    host = _host_ascii(host)
    if host.lower() in HOSTS_BLOQUEADOS:
        raise DestinoBloqueadoError(f"host bloqueado: {host}")
    porta = partes.port or (443 if partes.scheme == "https" else 80)
    literal = _host_literal_ip(host)
    if literal is not None:
        ips = [literal]
    else:
        try:
            enderecos = await asyncio.to_thread(socket.getaddrinfo, host, porta)
        except socket.gaierror as exc:
            raise DestinoBloqueadoError(f"não foi possível resolver o host: {host}") from exc
        if not enderecos:
            raise DestinoBloqueadoError(f"host sem endereço resolvido: {host}")
        ips = []
        for _familia, _tipo, _proto, _nome_can, endereco_socket in enderecos:
            ip = endereco_socket[0].split("%", 1)[0]  # remove o escopo de IPv6 link-local, se vier
            if ip not in ips:
                ips.append(ip)
    for ip in ips:
        if _ip_e_privado_ou_reservado(ip):
            raise DestinoBloqueadoError(f"destino privado/reservado bloqueado: {host} -> {ip}")
    return host, porta, ips[:MAX_IPS_POR_DESTINO]


def _requisicao_fixada(url: str, host: str, ip: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """URL com o IP validado, cabeçalho Host e SNI com o nome original, sem credenciais da URL."""
    partes = urlparse(url)
    netloc = _bracket(ip) + (f":{partes.port}" if partes.port else "")
    url_ip = partes._replace(netloc=netloc).geturl()
    host_header = _bracket(host) + (f":{partes.port}" if partes.port else "")
    # Connection: close -- o pool do httpcore indexa a conexão por (esquema, IP, porta); sem isso um
    # redirecionamento para OUTRO nome no mesmo IP reaproveitaria a conexão TLS do primeiro e o
    # certificado do segundo nome nunca seria verificado (o SNI só vale ao abrir a conexão).
    return url_ip, {"Host": host_header, "Connection": "close"}, {"sni_hostname": host}


async def _requisitar_com_limite(
    cliente: httpx.AsyncClient, metodo: str, url: str, headers: dict[str, str], extensoes: dict[str, str]
) -> httpx.Response:
    if metodo == "HEAD":
        return await cliente.head(url, headers=headers, extensions=extensoes)
    async with cliente.stream("GET", url, headers=headers, extensions=extensoes) as resposta:
        lido = 0
        async for pedaco in resposta.aiter_bytes():
            lido += len(pedaco)
            if lido > TAMANHO_MAXIMO_RESPOSTA_BYTES:
                break
        await resposta.aclose()
        return resposta


async def _requisitar_no_ip_validado(cliente: httpx.AsyncClient, url: str) -> httpx.Response:
    """Faz a requisição (HEAD, com GET de reserva) conectando só nos IPs já validados.

    Tenta os IPs validados em ordem (o IPv6 pode não ter rota, por exemplo) e só desiste
    quando a conexão falha em todos -- sem nunca pedir ao client que resolva o nome.
    """
    host, _porta, ips = await _resolver_e_validar(url)
    ultimo_erro: httpx.HTTPError | None = None
    for ip in ips:
        url_ip, headers, extensoes = _requisicao_fixada(url, host, ip)
        try:
            resposta = await _requisitar_com_limite(cliente, "HEAD", url_ip, headers, extensoes)
            if resposta.status_code >= 405:  # nem todo servidor aceita HEAD
                resposta = await _requisitar_com_limite(cliente, "GET", url_ip, headers, extensoes)
            return resposta
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            ultimo_erro = exc
    raise ultimo_erro or httpx.ConnectError("sem IP validado para conectar")


async def verificar_site(site: str | None, *, cliente: httpx.AsyncClient | None = None) -> dict:
    """Devolve {"ativo", "status_code", "url_final", "erro"}.

    ativo=None quando não há site pra checar (sem dado, não "fora do ar").
    ativo=False com erro="DestinoBloqueado" quando a URL aponta pra um
    destino interno/privado (ver DestinoBloqueadoError) -- tratado como
    falha de verificação, não como exceção que sobe pro chamador.
    """
    url = normalizar_url(site)
    if url is None:
        return {"ativo": None, "status_code": None, "url_final": None, "erro": None}

    cliente_proprio = cliente is None
    if cliente_proprio:
        cliente = httpx.AsyncClient(
            timeout=TIMEOUT_SEGUNDOS,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_keepalive_connections=0),  # sem reaproveitar conexões (ver _requisicao_fixada)
        )
    try:
        url_atual = url
        resposta: httpx.Response | None = None
        for _salto in range(MAX_REDIRECIONAMENTOS + 1):
            resposta = await _requisitar_no_ip_validado(cliente, url_atual)
            if resposta.is_redirect:
                proxima = resposta.headers.get("location")
                if not proxima:
                    break
                url_atual = str(httpx.URL(url_atual).join(proxima))
                continue
            break
        else:
            return {"ativo": False, "status_code": None, "url_final": None, "erro": "MuitosRedirecionamentos"}
    except DestinoBloqueadoError:
        return {"ativo": False, "status_code": None, "url_final": None, "erro": "DestinoBloqueado"}
    except httpx.HTTPError as exc:
        return {"ativo": False, "status_code": None, "url_final": None, "erro": type(exc).__name__}
    except (httpx.InvalidURL, ValueError):
        # URL malformada (ex.: colchete de IPv6 sem fechar): é dado ruim do prospect, não falha do job.
        return {"ativo": False, "status_code": None, "url_final": None, "erro": "UrlInvalida"}
    finally:
        if cliente_proprio:
            await cliente.aclose()

    return {
        "ativo": resposta.status_code < 400,
        "status_code": resposta.status_code,
        "url_final": url_atual,
        "erro": None,
    }

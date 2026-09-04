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
lido no fallback GET. Risco residual conhecido, não mitigado aqui: DNS
rebinding (o host podia resolver para um IP público na validação e mudar
para um IP privado bem na hora da conexão de verdade) -- mitigação completa
exigiria pinar o IP resolvido e conectar direto nele, fora do escopo desta
correção pontual.
"""

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
    return (
        endereco.is_private
        or endereco.is_loopback
        or endereco.is_link_local  # cobre 169.254.169.254 (metadata AWS/GCP/Azure)
        or endereco.is_multicast
        or endereco.is_reserved
        or endereco.is_unspecified
    )


def _validar_destino(url: str) -> None:
    """Levanta DestinoBloqueadoError se a URL usar esquema fora de http/https,
    tiver host vazio/bloqueado, ou resolver para IP privado/reservado."""
    partes = urlparse(url)
    if partes.scheme not in ESQUEMAS_PERMITIDOS:
        raise DestinoBloqueadoError(f"esquema não permitido: {partes.scheme or '(vazio)'}")
    host = partes.hostname
    if not host:
        raise DestinoBloqueadoError("URL sem host")
    if host.lower() in HOSTS_BLOQUEADOS:
        raise DestinoBloqueadoError(f"host bloqueado: {host}")
    try:
        enderecos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise DestinoBloqueadoError(f"não foi possível resolver o host: {host}") from exc
    if not enderecos:
        raise DestinoBloqueadoError(f"host sem endereço resolvido: {host}")
    for _familia, _tipo, _proto, _nome_can, endereco_socket in enderecos:
        ip_str = endereco_socket[0]
        if _ip_e_privado_ou_reservado(ip_str):
            raise DestinoBloqueadoError(f"destino privado/reservado bloqueado: {host} -> {ip_str}")


async def _requisitar_com_limite(cliente: httpx.AsyncClient, metodo: str, url: str) -> httpx.Response:
    if metodo == "HEAD":
        return await cliente.head(url)
    async with cliente.stream("GET", url) as resposta:
        lido = 0
        async for pedaco in resposta.aiter_bytes():
            lido += len(pedaco)
            if lido > TAMANHO_MAXIMO_RESPOSTA_BYTES:
                break
        await resposta.aclose()
        return resposta


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
            timeout=TIMEOUT_SEGUNDOS, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        )
    try:
        url_atual = url
        resposta: httpx.Response | None = None
        for _salto in range(MAX_REDIRECIONAMENTOS + 1):
            _validar_destino(url_atual)
            resposta = await _requisitar_com_limite(cliente, "HEAD", url_atual)
            if resposta.status_code >= 405:  # nem todo servidor aceita HEAD
                resposta = await _requisitar_com_limite(cliente, "GET", url_atual)
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
    finally:
        if cliente_proprio:
            await cliente.aclose()

    return {
        "ativo": resposta.status_code < 400,
        "status_code": resposta.status_code,
        "url_final": url_atual,
        "erro": None,
    }

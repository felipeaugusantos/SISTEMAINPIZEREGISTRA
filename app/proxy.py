from __future__ import annotations

from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from fastapi import Request

from app.settings import get_settings


def _peer_confiavel(request: Request) -> bool:
    if request.client is None:
        return False
    try:
        peer = ip_address(request.client.host)
    except ValueError:
        return False
    for item in get_settings().trusted_proxy_cidrs:
        try:
            if peer in ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def cliente_ip(request: Request) -> str:
    """Retorna o IP original somente quando o peer e um proxy confiavel."""
    if _peer_confiavel(request):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            candidato = forwarded.split(",", 1)[0].strip()
            try:
                return str(ip_address(candidato))
            except ValueError:
                pass
    return request.client.host if request.client else "desconhecido"


def host_publico(request: Request) -> str:
    host = request.url.hostname or ""
    if _peer_confiavel(request):
        forwarded = request.headers.get("x-forwarded-host", "")
        if forwarded:
            host = forwarded.split(",", 1)[0]
    return host.split(":", 1)[0].strip().lower()


def requisicao_https(request: Request | None = None) -> bool:
    settings = get_settings()
    if urlparse(settings.app_public_url).scheme.lower() == "https":
        return True
    if request is None:
        return settings.app_env.lower() == "production" or settings.admin_force_https
    if request.url.scheme.lower() == "https":
        return True
    return _peer_confiavel(request) and (
        request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        == "https"
    )

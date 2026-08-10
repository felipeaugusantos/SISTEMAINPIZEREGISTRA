from types import SimpleNamespace

from starlette.requests import Request

from app import proxy


def _request(peer: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers or [],
            "client": (peer, 12345),
            "server": ("localhost", 8000),
        }
    )


def test_proxy_confiavel_fornece_ip_host_e_https(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy,
        "get_settings",
        lambda: SimpleNamespace(
            trusted_proxy_cidrs=["127.0.0.1/32"],
            app_public_url="http://localhost:8000",
            app_env="development",
            admin_force_https=False,
        ),
    )
    request = _request(
        "127.0.0.1",
        [
            (b"x-forwarded-for", b"203.0.113.10, 127.0.0.1"),
            (b"x-forwarded-host", b"consulta.exemplo.com"),
            (b"x-forwarded-proto", b"https"),
        ],
    )

    assert proxy.cliente_ip(request) == "203.0.113.10"
    assert proxy.host_publico(request) == "consulta.exemplo.com"
    assert proxy.requisicao_https(request) is True


def test_proxy_nao_confiavel_nao_pode_forjar_cabecalhos(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy,
        "get_settings",
        lambda: SimpleNamespace(
            trusted_proxy_cidrs=["127.0.0.1/32"],
            app_public_url="http://localhost:8000",
            app_env="development",
            admin_force_https=False,
        ),
    )
    request = _request(
        "198.51.100.20",
        [
            (b"x-forwarded-for", b"203.0.113.10"),
            (b"x-forwarded-host", b"tenant-forjado.exemplo"),
            (b"x-forwarded-proto", b"https"),
        ],
    )

    assert proxy.cliente_ip(request) == "198.51.100.20"
    assert proxy.host_publico(request) == "localhost"
    assert proxy.requisicao_https(request) is False

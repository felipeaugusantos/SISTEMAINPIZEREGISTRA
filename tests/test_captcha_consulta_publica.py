"""CAPTCHA da consulta pública (Cloudflare Turnstile) -- pendência da Fase 12
(achado baixo: só honeypot + limite por IP), decisão do usuário em
29/09/2026: exigido no envio de dados que cria lead e no disparo de nova
pesquisa de marca. Desligado enquanto as chaves não estiverem configuradas.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.captcha as captcha
from app.api.confiabilidade import captcha_publico
from app.api.leads import router as router_leads
from app.api.pesquisas import router as router_pesquisas
from app.observability import _CSP_PADRAO

WEB = Path(__file__).resolve().parents[1] / "app" / "web"


def _configurar(monkeypatch: pytest.MonkeyPatch, site: str = "", secreta: str = "") -> None:
    configuracao = SimpleNamespace(turnstile_site_key=site, turnstile_secret_key=secreta, turnstile_timeout_seconds=5.0)
    monkeypatch.setattr(captcha, "get_settings", lambda: configuracao)
    monkeypatch.setattr("app.api.confiabilidade.get_settings", lambda: configuracao)


def _request(token: str | None = None):
    cabecalhos = {"X-Captcha-Token": token} if token is not None else {}
    return SimpleNamespace(headers=cabecalhos)


def test_sem_chaves_o_captcha_fica_desligado(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch)
    assert asyncio.run(captcha.exigir_captcha(_request())) is None
    assert asyncio.run(captcha_publico()) == {"provedor": "turnstile", "site_key": None}


def test_com_chaves_exige_o_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch, "site", "secreta")
    with pytest.raises(HTTPException) as erro:
        asyncio.run(captcha.exigir_captcha(_request()))
    assert erro.value.status_code == 400
    assert asyncio.run(captcha_publico())["site_key"] == "site"


def test_token_recusado_pela_cloudflare_bloqueia(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch, "site", "secreta")
    monkeypatch.setattr(captcha, "cliente_ip", lambda _request: "203.0.113.9")

    async def _recusar(_token, _ip):
        return False

    monkeypatch.setattr(captcha, "verificar_token_turnstile", _recusar)
    with pytest.raises(HTTPException) as erro:
        asyncio.run(captcha.exigir_captcha(_request("token-invalido")))
    assert erro.value.status_code == 400


def test_token_aceito_libera(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch, "site", "secreta")
    monkeypatch.setattr(captcha, "cliente_ip", lambda _request: "203.0.113.9")
    recebidos = []

    async def _aceitar(token, ip):
        recebidos.append((token, ip))
        return True

    monkeypatch.setattr(captcha, "verificar_token_turnstile", _aceitar)
    assert asyncio.run(captcha.exigir_captcha(_request("token-bom"))) is None
    assert recebidos == [("token-bom", "203.0.113.9")]


def test_falha_de_rede_conta_como_nao_verificado(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch, "site", "secreta")

    class _ClienteQuebrado:
        def __init__(self, *_args, **_kwargs) -> None: ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

        async def post(self, *_args, **_kwargs):
            raise OSError("sem rede")

    monkeypatch.setattr(captcha.httpx, "AsyncClient", _ClienteQuebrado)
    assert asyncio.run(captcha.verificar_token_turnstile("t", None)) is False


@pytest.mark.parametrize(("router", "caminho"), [(router_leads, "/v1/leads"), (router_pesquisas, "/v1/pesquisas-marca")])
def test_endpoints_publicos_exigem_o_captcha(router, caminho: str) -> None:
    rota = next(r for r in router.routes if getattr(r, "path", None) == caminho and "POST" in r.methods)
    assert any(dep.call is captcha.exigir_captcha for dep in rota.dependant.dependencies)


def test_csp_libera_so_a_origem_do_turnstile() -> None:
    assert "script-src 'self' https://challenges.cloudflare.com" in _CSP_PADRAO
    assert "frame-src https://challenges.cloudflare.com" in _CSP_PADRAO


def test_formularios_publicos_carregam_o_widget() -> None:
    for pagina in ("index", "buscar-gratuita"):
        html = (WEB / f"{pagina}.html").read_text(encoding="utf-8")
        assert "/static/captcha-publico.js?v=1" in html, pagina
        assert "data-captcha" in html, pagina
    for script in ("app.js", "buscar-gratuita.js"):
        assert "zeCaptcha?.cabecalhos()" in (WEB / "static" / script).read_text(encoding="utf-8"), script

"""Fase 19.1 (white-label): o painel administrativo mostra nome, logo e cor
do escritório do usuário logado. Antes, admin-shell.js tinha "Zé Registra®"
e o personagem da plataforma fixos, e os títulos das abas terminavam em
"— Zé Registra" para qualquer escritório.

Isolados e determinísticos: FakeSession (tests/conftest.py), sem banco real.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import obter_usuario_atual
from app.database import get_session
from app.main import app
from app.marca import css_marca, marca_organizacao
from app.models import Organizacao
from app.settings import get_settings
from tests.conftest import FakeSession, auth_override, usuario_teste

SHELL = Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "admin-shell.js"


def _organizacao(**kwargs: object) -> Organizacao:
    base: dict = {"id": 2, "nome": "Escritorio Beta Ltda", "slug": "escritorio-beta", "branding": None}
    base.update(kwargs)
    return Organizacao(**base)


def test_organizacao_padrao_sem_configuracao_mantem_a_identidade_da_plataforma() -> None:
    padrao = _organizacao(id=1, nome="Zé Registra", slug=get_settings().default_organization_slug)
    marca = marca_organizacao(padrao)
    assert marca["propria"] is False
    assert marca["nome"] == "Zé Registra"


def test_outro_escritorio_tem_marca_propria_mesmo_sem_configurar() -> None:
    marca = marca_organizacao(_organizacao())
    assert marca == {"nome": "Escritorio Beta Ltda", "logo_url": None, "cor_primaria": None, "propria": True}


def test_nome_exibido_logo_e_cor_configurados_tem_precedencia() -> None:
    org = _organizacao(
        branding={"nome_exibido": "Beta Marcas", "logo_url": "/v1/tenant/logo", "cor_primaria": "#123abc"}
    )
    assert marca_organizacao(org) == {
        "nome": "Beta Marcas",
        "logo_url": "/v1/tenant/logo",
        "cor_primaria": "#123abc",
        "propria": True,
    }


def test_cor_invalida_e_ignorada() -> None:
    org = _organizacao(branding={"cor_primaria": "red;}body{display:none"})
    assert marca_organizacao(org)["cor_primaria"] is None
    assert css_marca(org.branding, painel=True) == ""


def test_css_do_painel_aplica_cor_e_libera_a_logo_propria() -> None:
    css = css_marca({"cor_primaria": "#123abc", "logo_url": "/v1/tenant/logo"}, painel=True)
    assert ":root{--forest:#123abc}" in css
    assert ".admin-sidebar-brand img.brand-avatar" in css


def test_endpoint_de_css_do_painel_usa_a_organizacao_do_usuario() -> None:
    org = _organizacao(branding={"cor_primaria": "#123abc"})

    async def _sessao():
        yield FakeSession(objetos_get=[org])

    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())
    try:
        resposta = TestClient(app).get("/v1/auth/me/marca.css")
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/css")
    assert ":root{--forest:#123abc}" in resposta.text


def test_shell_do_painel_aplica_a_marca_do_escritorio() -> None:
    script = SHELL.read_text(encoding="utf-8")
    assert "function aplicarMarcaDoEscritorio" in script
    assert "aplicarMarcaDoEscritorio(user.organizacao?.marca)" in script
    assert "/v1/auth/me/marca.css" in script

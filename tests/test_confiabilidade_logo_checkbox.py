"""Achado do usuário (30/09/2026) na tela Confiabilidade e LGPD:

- a prévia do logotipo enviado ficava vazia: ela usava /v1/tenant/logo, que
  identifica o escritório pelo domínio e, em produção, recusa domínio não
  verificado (a organização padrão não tem domínio verificado). Passa a usar
  /v1/auth/me/logo, autenticado pela organização do usuário logado -- o
  mesmo endereço vai para a marca do painel (Fase 19.1);
- os checkboxes de consentimento não tinham estilo no painel e apareciam
  gigantes, desalinhados do texto.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.auth_routes import logo_url_painel
from app.auth import obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Organizacao
from tests.conftest import FakeSession, auth_override, usuario_teste

WEB = Path(__file__).resolve().parents[1] / "app" / "web"


def _organizacao(branding: dict | None) -> Organizacao:
    return Organizacao(id=1, nome="Zé Registra", slug="ze-registra", branding=branding)


def test_endpoint_autenticado_serve_a_logo_da_organizacao_do_usuario(tmp_path: Path) -> None:
    arquivo = tmp_path / "logo.png"
    arquivo.write_bytes(b"\x89PNG-teste")
    org = _organizacao({"logo_asset": {"localizacao": str(arquivo), "sha256": "abc"}})

    async def _sessao():
        yield FakeSession(objetos_get=[org])

    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())
    try:
        resposta = TestClient(app).get("/v1/auth/me/logo")
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200
    assert resposta.content == b"\x89PNG-teste"
    assert resposta.headers["content-type"] == "image/png"


def test_sem_logo_enviada_responde_404() -> None:
    async def _sessao():
        yield FakeSession(objetos_get=[_organizacao({})])

    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())
    try:
        resposta = TestClient(app).get("/v1/auth/me/logo")
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 404


def test_logo_do_painel_usa_o_endereco_autenticado() -> None:
    enviada = _organizacao({"logo_asset": {"localizacao": "data/x.png", "sha256": "12de307d318542641fb6"}, "logo_url": "/v1/tenant/logo?v=12de"})
    assert logo_url_painel(enviada) == "/v1/auth/me/logo?v=12de307d31854264"
    externa = _organizacao({"logo_url": "https://cdn.exemplo.test/logo.png"})
    assert logo_url_painel(externa) == "https://cdn.exemplo.test/logo.png"
    assert logo_url_painel(_organizacao(None)) is None


def test_previa_da_tela_usa_o_endereco_autenticado() -> None:
    script = (WEB / "static" / "admin-confiabilidade.js").read_text(encoding="utf-8")
    assert '.replace("/v1/tenant/logo", "/v1/auth/me/logo")' in script
    html = (WEB / "admin-confiabilidade.html").read_text(encoding="utf-8")
    assert "/static/admin-confiabilidade.js?v=10" in html


def test_checkbox_de_consentimento_tem_o_estilo_padrao() -> None:
    html = (WEB / "admin-confiabilidade.html").read_text(encoding="utf-8")
    assert "/static/admin-confiabilidade.css?v=1" in html
    css = (WEB / "static" / "admin-confiabilidade.css").read_text(encoding="utf-8")
    assert "width: 18px" in css
    assert ".consent-label" in css

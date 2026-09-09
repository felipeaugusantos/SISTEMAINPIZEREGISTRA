from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.requests import Request

from app.api.confiabilidade import TAMANHO_MAXIMO_LOGO, logo_publico, normalizar_logo
from app.auth import UsuarioAutenticado, hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import EventoAuditoria, Organizacao
from tests.conftest import FakeSession, auth_override


def _usuario(organizacao_id: int = 7) -> UsuarioAutenticado:
    return UsuarioAutenticado(
        id=3,
        nome="Admin",
        usuario="admin",
        email="admin@teste.local",
        perfil="administrador",
        permissoes=frozenset(),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash=hash_token("csrf-teste"),
        organizacao_id=organizacao_id,
    )


def _png(largura: int = 80, altura: int = 60) -> bytes:
    saida = BytesIO()
    Image.new("RGBA", (largura, altura), (0, 107, 79, 255)).save(saida, format="PNG")
    return saida.getvalue()


def _cliente(session: FakeSession) -> TestClient:
    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[obter_usuario_atual] = auth_override(_usuario())
    return TestClient(app)


def test_normalizacao_remove_metadados_e_mantem_dimensoes() -> None:
    normalizado, largura, altura = normalizar_logo(_png())

    assert normalizado.startswith(b"\x89PNG")
    assert (largura, altura) == (80, 60)
    with Image.open(BytesIO(normalizado)) as imagem:
        assert imagem.format == "PNG"
        assert not imagem.info


@pytest.mark.parametrize(
    "conteudo",
    [b"nao-e-imagem", b"x" * (TAMANHO_MAXIMO_LOGO + 1), _png(16, 16), _png(2001, 40)],
    ids=["nao-imagem", "acima-de-1mb", "pequena", "dimensao-excessiva"],
)
def test_normalizacao_rejeita_arquivo_inseguro(conteudo: bytes) -> None:
    with pytest.raises(ValueError):
        normalizar_logo(conteudo)


def test_upload_de_logo_preserva_branding_e_nao_expoe_caminho(monkeypatch) -> None:
    org = Organizacao(
        id=7,
        nome="Tenant 7",
        slug="tenant-7",
        branding={"proposta": {"titulo": "Preservar"}, "clicksign": {"api_token_enc": "segredo"}},
    )
    session = FakeSession(objetos_get=[org])
    localizacao = "data/uploads/branding/org-7/logo.png"
    monkeypatch.setattr("app.api.confiabilidade.save_bytes", lambda _key, _body: localizacao)
    cliente = _cliente(session)
    try:
        resposta = cliente.post(
            "/v1/admin/confiabilidade/identidade/logo",
            headers={"X-CSRF-Token": "csrf-teste"},
            files={"arquivo": ("logo.png", _png(), "image/png")},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 201, resposta.text
    assert "localizacao" not in resposta.json()
    assert org.branding["proposta"] == {"titulo": "Preservar"}
    assert org.branding["clicksign"] == {"api_token_enc": "segredo"}
    assert org.branding["logo_url"].startswith("/v1/tenant/logo?v=")
    assert org.branding["logo_asset"]["localizacao"] == localizacao
    evento = next(item for item in session.adicionados if isinstance(item, EventoAuditoria))
    assert evento.acao == "ENVIAR_LOGO_IDENTIDADE_VISUAL"
    assert evento.organizacao_id == 7


def test_upload_invalido_nao_grava_configuracao(monkeypatch) -> None:
    org = Organizacao(id=7, nome="Tenant 7", slug="tenant-7", branding={})
    session = FakeSession(objetos_get=[org])
    gravou = False

    def salvar(_key, _body):
        nonlocal gravou
        gravou = True

    monkeypatch.setattr("app.api.confiabilidade.save_bytes", salvar)
    cliente = _cliente(session)
    try:
        resposta = cliente.post(
            "/v1/admin/confiabilidade/identidade/logo",
            headers={"X-CSRF-Token": "csrf-teste"},
            files={"arquivo": ("logo.svg", b"<svg><script>alert(1)</script></svg>", "image/svg+xml")},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 422
    assert gravou is False
    assert session.commits == 0
    assert org.branding == {}


@pytest.mark.asyncio
async def test_logo_publico_usa_tenant_resolvido_sem_expor_storage(monkeypatch) -> None:
    org = SimpleNamespace(
        branding={"logo_asset": {"localizacao": "s3://bucket-interno/tenant-41/logo.png"}}
    )

    async def resolver(_request, _session):
        return org

    monkeypatch.setattr("app.tenancy.resolver_organizacao_publica", resolver)
    monkeypatch.setattr("app.api.confiabilidade.read_bytes", lambda _local: _png())
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/v1/tenant/logo",
            "query_string": b"",
            "headers": [(b"host", b"tenant-41.test")],
            "server": ("tenant-41.test", 443),
            "client": ("127.0.0.1", 1234),
        }
    )

    resposta = await logo_publico(request, FakeSession())

    assert resposta.media_type == "image/png"
    assert resposta.headers["x-content-type-options"] == "nosniff"
    assert b"bucket-interno" not in resposta.body

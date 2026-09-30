from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.leads import listar_leads
from app.api.saas import PlanoInput
from app.auth import UsuarioAutenticado, obter_usuario_atual
from app.main import app
from app.tenancy import resolver_organizacao_publica
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste


@pytest.mark.asyncio
async def test_consulta_de_leads_sempre_filtra_a_organizacao() -> None:
    class RecordingSession(FakeSession):
        def __init__(self) -> None:
            super().__init__(
                [
                    FakeResult(scalar=0),
                    FakeResult(scalar=0),
                    FakeResult(itens=[]),
                    FakeResult(itens=[]),
                    FakeResult(scalar=0),
                ]
            )
            self.statements = []

        async def execute(self, statement, *args, **kwargs):
            self.statements.append(statement)
            return await super().execute(statement, *args, **kwargs)

    session = RecordingSession()
    usuario = UsuarioAutenticado(
        id=7,
        nome="Operador",
        usuario="operador",
        email="op@empresa.test",
        perfil="administrador",
        permissoes=frozenset(),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash="",
        organizacao_id=42,
        organizacao_slug="empresa",
    )
    resposta = await listar_leads(session, usuario, None, None, 50, 0)
    assert resposta.total == 0
    for statement in session.statements:
        if "FROM leads" in str(statement):
            assert "leads.organizacao_id" in str(statement)
            assert 42 in statement.compile().params.values()


def test_modulo_do_plano_limita_permissao_individual() -> None:
    usuario = UsuarioAutenticado(
        id=2,
        nome="Operador",
        usuario="operador",
        email="op@empresa.test",
        perfil="operador",
        permissoes=frozenset({"risk.view"}),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash="",
        organizacao_id=2,
        organizacao_slug="empresa",
        modulos_plano=frozenset({"consulta"}),
    )
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        assert TestClient(app).get("/admin/risco").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_painel_saas_exige_superadministrador() -> None:
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())
    try:
        assert TestClient(app).get("/admin/saas").status_code == 403
    finally:
        app.dependency_overrides.clear()

    superadmin = UsuarioAutenticado(
        id=1,
        nome="Superadmin",
        usuario="admin",
        email="admin@teste.local",
        perfil="administrador",
        permissoes=frozenset(),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash="",
        superadmin=True,
    )
    app.dependency_overrides[obter_usuario_atual] = auth_override(superadmin)
    try:
        assert TestClient(app).get("/admin/saas").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_preflight_permite_integracao_publica_sem_credenciais_de_cookie() -> None:
    resposta = TestClient(app).options(
        "/v1/pesquisas-marca",
        headers={
            "Origin": "https://cliente.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-integration-key",
        },
    )
    assert resposta.status_code == 200
    assert resposta.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in resposta.headers


def test_plano_rejeita_limite_nao_aplicado_e_valor_negativo() -> None:
    valido = PlanoInput(nome="Básico", codigo="basico", limites={"usuarios": 5, "pesquisas_mes": 100})
    assert valido.limites["usuarios"] == 5

    with pytest.raises(ValidationError, match="não implementados"):
        PlanoInput(nome="Básico", codigo="basico", limites={"armazenamento_mb": 500})
    with pytest.raises(ValidationError, match="negativos"):
        PlanoInput(nome="Básico", codigo="basico", limites={"usuarios": -1})


@pytest.mark.asyncio
async def test_dominio_nao_verificado_nao_resolve_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    class RecordingSession(FakeSession):
        def __init__(self) -> None:
            super().__init__([FakeResult(), FakeResult()])

    session = RecordingSession()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", b"tenant.example.test")],
            "server": ("tenant.example.test", 443),
            "client": ("127.0.0.1", 12345),
        }
    )
    monkeypatch.setattr(
        "app.tenancy.get_settings",
        lambda: SimpleNamespace(
            integration_auth_enabled=False,
            inpi_integration_token="",
            app_env="production",
        ),
    )

    with pytest.raises(HTTPException) as erro:
        await resolver_organizacao_publica(request, session)

    assert erro.value.status_code == 404
    consulta_dominio = next(stmt for stmt in session.executados if "dominios_organizacao" in str(stmt))
    assert "verificado_em IS NOT NULL" in str(consulta_dominio)


def _requisicao_publica(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", host.encode())],
            "server": (host, 443),
            "client": ("127.0.0.1", 12345),
        }
    )


def _configuracao_producao() -> SimpleNamespace:
    return SimpleNamespace(
        integration_auth_enabled=False,
        inpi_integration_token="",
        app_env="production",
        app_public_url="https://app.zeregistra.com.br",
        default_organization_slug="ze-registra",
    )


@pytest.mark.asyncio
async def test_endereco_da_plataforma_resolve_a_organizacao_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    # Achado de 30/09/2026: sem domínio verificado, o relatório da pesquisa, a
    # consulta pública e a marca pública davam 404 em app.zeregistra.com.br.
    padrao = SimpleNamespace(
        id=1,
        nome="Zé Registra",
        slug="ze-registra",
        status="ativa",
        plano=SimpleNamespace(codigo="profissional", modulos=["pesquisa"], limites={}),
        modulos_liberados=None,
        branding={},
        politica_privacidade_versao="1.0",
    )
    session = FakeSession([FakeResult(), FakeResult(scalar=padrao)])
    monkeypatch.setattr("app.tenancy.get_settings", _configuracao_producao)

    atual = await resolver_organizacao_publica(_requisicao_publica("app.zeregistra.com.br"), session)

    assert atual.id == 1
    assert atual.slug == "ze-registra"


@pytest.mark.asyncio
async def test_outro_host_sem_dominio_verificado_continua_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession([FakeResult(), FakeResult()])
    monkeypatch.setattr("app.tenancy.get_settings", _configuracao_producao)

    with pytest.raises(HTTPException) as erro:
        await resolver_organizacao_publica(_requisicao_publica("outro.exemplo.test"), session)

    assert erro.value.status_code == 404

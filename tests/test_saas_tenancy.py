import pytest
from fastapi.testclient import TestClient

from app.api.leads import listar_leads
from app.auth import UsuarioAutenticado, obter_usuario_atual
from app.main import app
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

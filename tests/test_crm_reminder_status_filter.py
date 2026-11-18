import pytest
from fastapi.testclient import TestClient

from app.auth import obter_usuario_atual
from app.database import get_session
from app.main import app
from tests.conftest import FakeResult, FakeSession, auth_override


@pytest.mark.parametrize(
    "status,expected",
    [(None, "pendente"), ("pendente", "pendente"), ("concluido", "concluido"), ("cancelado", "cancelado"), ("", None)],
)
def test_situacao_todos_explicitamente_sem_filtro_preserva_padrao(status, expected):
    session = FakeSession(
        [FakeResult(scalar=0), FakeResult(), FakeResult(itens=[(0, 0, 0)]), FakeResult(scalar=0), FakeResult()]
    )

    async def get_test_session():
        yield session

    app.dependency_overrides[get_session] = get_test_session
    app.dependency_overrides[obter_usuario_atual] = auth_override()
    try:
        response = TestClient(app).get("/v1/admin/crm/lembretes", params={} if status is None else {"status": status})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    sql = str(session.executados[0].compile(compile_kwargs={"literal_binds": True}))
    assert "lembretes_crm.organizacao_id = 1" in sql
    if expected:
        assert f"lembretes_crm.status = '{expected}'" in sql
    else:
        assert "lembretes_crm.status =" not in sql


@pytest.mark.parametrize("status", ["inexistente", "TODOS"])
def test_situacao_invalida_continua_rejeitada(status):
    app.dependency_overrides[obter_usuario_atual] = auth_override()
    try:
        response = TestClient(app).get("/v1/admin/crm/lembretes", params={"status": status})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422

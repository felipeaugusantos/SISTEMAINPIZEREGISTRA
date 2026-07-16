from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from tests.conftest import FakeResult, sessao_override


def test_health_ok() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult())
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development", "database": "ok"}


class SessaoComFalha:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("banco indisponível")


async def _sessao_com_falha():
    yield SessaoComFalha()


def test_health_banco_indisponivel() -> None:
    app.dependency_overrides[get_session] = _sessao_com_falha
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["database"] == "error"


class EmptyResult:
    def scalar_one_or_none(self) -> None:
        return None


class EmptySession:
    async def execute(self, _query) -> EmptyResult:
        return EmptyResult()


async def empty_session_override():
    yield EmptySession()


def test_process_not_found() -> None:
    app.dependency_overrides[get_session] = empty_session_override
    try:
        response = TestClient(app).get("/v1/processos/000000000")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Processo não encontrado"}

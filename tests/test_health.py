from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from app.models import RpiImportacao, RpiSyncEstado, RpiSyncExecucao
from app.settings import get_settings
from tests.conftest import FakeResult, FakeSession, sessao_override


def test_health_ok() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult())
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert {chave: payload[chave] for chave in ("status", "environment", "database")} == {
        "status": "ok",
        "environment": get_settings().app_env,
        "database": "ok",
    }
    if "redis" in payload:
        assert payload["redis"]["status"] == "ok"


def test_request_id_recebido_e_devolvido() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult())
    try:
        response = TestClient(app).get("/health", headers={"X-Request-ID": "crm-123"})
    finally:
        app.dependency_overrides.clear()

    assert response.headers["X-Request-ID"] == "crm-123"


def test_request_id_invalido_e_substituido() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult())
    try:
        response = TestClient(app).get("/health", headers={"X-Request-ID": "invalido com espaco"})
    finally:
        app.dependency_overrides.clear()

    assert response.headers["X-Request-ID"]
    assert response.headers["X-Request-ID"] != "invalido com espaco"


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


def test_health_rpi_expoe_estado_operacional() -> None:
    agora = datetime.now(UTC)
    estado = RpiSyncEstado(
        id=1,
        status="atualizado",
        ultima_rpi_oficial=2901,
        ultima_rpi_local=2901,
        falhas_consecutivas=0,
    )
    importacao = RpiImportacao(
        numero_rpi=2901,
        tipo="marca",
        registros_processados=40_888,
        titulares_processados=41_267,
        classes_processadas=73_467,
        movimentacoes_processadas=41_088,
        status_integridade="ok",
        anomalias=[],
        importado_em=agora - timedelta(hours=1),
    )
    execucao = RpiSyncExecucao(
        id=1,
        origem="automatica",
        status="concluida",
        solicitado_em=agora - timedelta(minutes=10),
        iniciado_em=agora - timedelta(minutes=8),
        finalizado_em=agora - timedelta(minutes=2),
    )

    class SessaoRpi(FakeSession):
        async def get(self, *_args, **_kwargs):
            return estado

        async def scalar(self, *_args, **_kwargs):
            return 2

    sessao = SessaoRpi([FakeResult(scalar=importacao), FakeResult(scalar=execucao)])

    async def override():
        yield sessao

    app.dependency_overrides[get_session] = override
    try:
        response = TestClient(app).get("/health/rpi")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["ultima_rpi_importada"] == 2901
    assert response.json()["registros_ultima_importacao"] == 40_888


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

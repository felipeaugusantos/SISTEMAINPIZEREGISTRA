from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from app.public_report_tokens import emitir_token_relatorio
from tests.conftest import FakeResult, sessao_override

TOKEN = "token-de-integracao-valido-com-mais-de-32-caracteres"


def _ativar_bearer(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.security.get_settings",
        lambda: SimpleNamespace(
            integration_auth_enabled=True,
            inpi_integration_token=TOKEN,
        ),
    )


def test_health_permanece_publico_com_bearer_ativo(monkeypatch) -> None:
    _ativar_bearer(monkeypatch)

    # O teste de health não precisa de banco; mantém o foco no escopo da autenticação.
    class Session:
        async def execute(self, *_args, **_kwargs) -> None:
            return None

    async def session_override():
        yield Session()

    app.dependency_overrides[get_session] = session_override
    try:
        assert TestClient(app).get("/health").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_relatorio_rejeita_requisicao_sem_token() -> None:
    resposta = TestClient(app).get("/v1/pesquisas-marca/qualquer/relatorio")

    assert resposta.status_code == 401
    assert resposta.headers["www-authenticate"] == "Report-Token"


def test_relatorio_rejeita_token_incorreto() -> None:
    resposta = TestClient(app).get(
        "/v1/pesquisas-marca/qualquer/relatorio",
        headers={"X-Report-Token": "incorreto"},
    )

    assert resposta.status_code == 401


def test_relatorio_aceita_token_temporario_correto() -> None:
    token = emitir_token_relatorio("qualquer", 1)
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    try:
        resposta = TestClient(app).get(
            "/v1/pesquisas-marca/qualquer/relatorio",
            headers={"X-Report-Token": token},
        )
    finally:
        app.dependency_overrides.clear()

    # Passou pelo token temporário e alcançou a consulta da pesquisa.
    assert resposta.status_code == 404


def test_chave_global_em_query_string_nao_autoriza_relatorio() -> None:
    resposta = TestClient(app).get(
        "/v1/pesquisas-marca/qualquer/relatorio",
        params={"chave_integracao": TOKEN},
    )

    assert resposta.status_code == 401

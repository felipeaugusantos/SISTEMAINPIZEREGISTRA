from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app

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
    from app.database import get_session

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


def test_pesquisa_rejeita_requisicao_sem_token(monkeypatch) -> None:
    _ativar_bearer(monkeypatch)
    resposta = TestClient(app).get("/v1/pesquisas-marca/qualquer/relatorio")

    assert resposta.status_code == 401
    assert resposta.headers["www-authenticate"] == "Bearer"


def test_pesquisa_rejeita_token_incorreto(monkeypatch) -> None:
    _ativar_bearer(monkeypatch)
    resposta = TestClient(app).get(
        "/v1/pesquisas-marca/qualquer/relatorio",
        headers={"Authorization": "Bearer incorreto"},
    )

    assert resposta.status_code == 401


def test_pesquisa_aceita_token_correto(monkeypatch) -> None:
    _ativar_bearer(monkeypatch)
    resposta = TestClient(app).get(
        "/v1/pesquisas-marca/qualquer/relatorio",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    # Passou pela autenticação e alcançou a consulta do identificador da pesquisa.
    assert resposta.status_code == 404

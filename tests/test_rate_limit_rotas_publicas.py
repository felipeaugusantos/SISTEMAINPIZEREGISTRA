import pytest
from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from tests.conftest import FakeSession

# Auditoria de seguranca (13/10/2026, item 9): estas rotas publicas eram as unicas
# da area de autenticacao/privacidade sem limite de taxa.


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


def _cliente() -> TestClient:
    async def _sessao():
        yield FakeSession([])

    app.dependency_overrides[get_session] = _sessao
    # Erros internos das chamadas anteriores ao limite nao interessam aqui.
    return TestClient(app, raise_server_exceptions=False)


def _statuses(cliente: TestClient, url: str, corpo: dict, tentativas: int) -> list[int]:
    return [cliente.post(url, json=corpo).status_code for _ in range(tentativas)]


@pytest.mark.parametrize(
    ("url", "corpo"),
    [
        (
            "/v1/auth/recuperacao/redefinir",
            {"token": "t" * 30, "nova_senha": "SenhaForte123!x"},
        ),
        (
            "/v1/auth/convites/aceitar",
            {"token": "t" * 30, "nome": "Pessoa Teste", "usuario": "pessoa.teste", "senha": "SenhaForte123!x"},
        ),
        (
            "/v1/privacidade/confirmar-exclusao",
            {"token": "t" * 30},
        ),
    ],
)
def test_rota_publica_responde_429_apos_dez_tentativas(url: str, corpo: dict) -> None:
    codigos = _statuses(_cliente(), url, corpo, 11)
    assert 429 not in codigos[:10]
    assert codigos[10] == 429

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.leads import limitar_admin, limitar_leads
from app.database import get_session
from app.main import app
from app.models import Lead, StatusLead
from app.settings import get_settings
from tests.conftest import FakeResult, sessao_override

_settings = get_settings()
CREDENCIAIS_OK = (_settings.admin_username, _settings.admin_password)


@pytest.fixture(autouse=True)
def _reset_estado() -> None:
    limitar_leads.limpar()
    limitar_admin.limpar()
    yield
    app.dependency_overrides.clear()
    limitar_leads.limpar()
    limitar_admin.limpar()


def _payload(**overrides: object) -> dict[str, object]:
    base = {
        "nome": "Fulano de Tal",
        "email": "fulano@example.com",
        "telefone": "11999998888",
        "marca": "ACME",
        "processo_numero": "123456789",
        "origem": "processo",
        "aceite_privacidade": True,
        "website": "",
    }
    base.update(overrides)
    return base


def test_honeypot_rejeita_envio() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    resposta = TestClient(app).post("/v1/leads", json=_payload(website="http://bot"))
    assert resposta.status_code == 400


def test_email_invalido_retorna_422() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    resposta = TestClient(app).post("/v1/leads", json=_payload(email="sem-arroba"))
    assert resposta.status_code == 422


def test_consentimento_obrigatorio() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    resposta = TestClient(app).post("/v1/leads", json=_payload(aceite_privacidade=False))
    assert resposta.status_code == 422


def test_lead_criado_sem_marca() -> None:
    # A busca é desacoplada da coleta de PII: 'marca' passou a ser opcional.
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    payload = _payload()
    del payload["marca"]
    resposta = TestClient(app).post("/v1/leads", json=payload)
    assert resposta.status_code == 201
    assert resposta.json()["id"] == 1


def test_rate_limit_bloqueia_excesso() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    cliente = TestClient(app)
    # O honeypot devolve 400 depois do limitador; as 10 primeiras contam no limite.
    for _ in range(10):
        assert cliente.post("/v1/leads", json=_payload(website="http://bot")).status_code == 400
    assert cliente.post("/v1/leads", json=_payload(website="http://bot")).status_code == 429


def test_admin_sem_credenciais() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    assert TestClient(app).get("/v1/admin/leads").status_code == 401


def test_admin_credenciais_invalidas() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    resposta = TestClient(app).get("/v1/admin/leads", auth=("admin", "errada"))
    assert resposta.status_code == 401


def test_admin_lista_com_credenciais() -> None:
    lead = Lead(
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        processo_numero="123456789",
        origem="processo",
        tipo_interesse=None,
        status=StatusLead.NOVO,
    )
    lead.id = 1
    lead.criado_em = datetime.now(UTC)
    lead.atualizado_em = datetime.now(UTC)

    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=1),
        FakeResult(itens=[lead]),
        FakeResult(itens=[(StatusLead.NOVO, 1)]),
    )
    resposta = TestClient(app).get("/v1/admin/leads", auth=CREDENCIAIS_OK)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["itens"][0]["email"] == "fulano@example.com"
    assert corpo["itens"][0]["processo_numero"] == "123456789"
    assert corpo["itens"][0]["origem"] == "processo"
    assert corpo["por_status"]["novo"] == 1

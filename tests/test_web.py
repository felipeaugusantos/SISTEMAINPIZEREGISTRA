from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings


def test_home_page() -> None:
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "Encontre um processo pelo nome" in response.text
    assert 'id="search-form"' in response.text
    assert "Pesquise sem cadastro" in response.text
    assert 'id="lead-cta"' in response.text


def test_process_detail_page() -> None:
    response = TestClient(app).get("/processos/935977333")

    assert response.status_code == 200
    assert 'id="process-detail"' in response.text
    assert "Movimentações na RPI" in response.text
    assert 'id="detail-contact-cta"' in response.text


def test_admin_leads_requires_authentication() -> None:
    client = TestClient(app)
    assert client.get("/admin/leads").status_code == 401

    settings = get_settings()
    response = client.get(
        "/admin/leads",
        auth=(settings.admin_username, settings.admin_password),
    )

    assert response.status_code == 200
    assert "Leads de pesquisa" in response.text


def test_privacy_page() -> None:
    response = TestClient(app).get("/privacidade")

    assert response.status_code == 200
    assert "Aviso de privacidade" in response.text

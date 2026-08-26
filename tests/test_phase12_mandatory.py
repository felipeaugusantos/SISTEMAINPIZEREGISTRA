"""Gates de regressão da Fase 12 (infraestrutura e contratos críticos)."""

from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.database import get_session
from app.main import app
from tests.conftest import FakeResult, sessao_override


def test_health_db_expoe_latencia_e_nao_vaza_detalhes() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult())
    try:
        response = TestClient(app).get("/health/db")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "latencia_ms" in response.json()


def test_health_queue_retorna_estado_operacional(monkeypatch) -> None:
    async def fila():
        return {
            "status": "ok",
            "pendentes": 2,
            "falhas": 0,
            "processando": 1,
            "retries_aguardando": 0,
            "metricas": {},
        }

    monkeypatch.setattr(main_module, "status_fila", fila)
    response = TestClient(app).get("/health/queue")
    assert response.status_code == 200
    assert response.json()["pendentes"] == 2


def test_metrics_prometheus_tem_requisicoes_erros_e_fila(monkeypatch) -> None:
    async def fila():
        return {
            "status": "ok",
            "pendentes": 3,
            "falhas": 1,
            "processando": 0,
            "retries_aguardando": 2,
            "metricas": {},
        }

    monkeypatch.setattr(main_module, "status_fila", fila)
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[(12, 2, 18.5)]))
    try:
        response = TestClient(app).get("/metrics")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "ze_registra_http_requests_total 12" in response.text
    assert "ze_registra_http_errors_total 2" in response.text
    assert "ze_registra_queue_pending 3" in response.text


def test_matriz_de_testes_obrigatorios_permanece_no_repositorio() -> None:
    obrigatorios = {
        "tests/test_bearer_auth.py",
        "tests/test_permissions.py",
        "tests/test_saas_rls_postgres.py",
        "tests/test_rpi_integrity.py",
        "tests/test_queueing.py",
        "tests/test_phase4_proposals.py",
        "tests/test_phase6_documents.py",
        "tests/test_phase7_operations.py",
        "tests/test_financeiro.py",
        "tests/test_producao.py",
        "tests/test_search_benchmark.py",
        "tests/test_search_ranking.py",
        "tests/test_auditing.py",
    }
    ausentes = [arquivo for arquivo in obrigatorios if not Path(arquivo).exists()]
    assert not ausentes, f"Suítes obrigatórias ausentes: {ausentes}"

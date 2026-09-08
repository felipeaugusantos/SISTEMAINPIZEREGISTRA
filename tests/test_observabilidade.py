from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import EventoOperacional
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

# --- Achado FASE6-4 da auditoria (04/09/2026): dead-letter queue visível e
# tratável no painel admin (antes só existia a contagem em status_fila). ---


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


def _override_session(session: FakeSession):
    async def _gen():
        yield session

    return _gen


def _sessao_tech(*resultados: FakeResult) -> FakeSession:
    session = FakeSession(list(resultados))
    usuario = usuario_teste(perfil="administrador")
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return session


async def _falhas_falsas(*, limite: int = 50, deslocamento: int = 0) -> list[dict]:
    return [{"id": "job-1", "tipo": "prospeccao.enriquecer_prospect", "tentativas": 3, "_bruto": "..."}]


def test_listar_fila_falhas_remove_campo_interno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.observabilidade.listar_falhas", _falhas_falsas)
    _sessao_tech()

    resposta = TestClient(app).get("/v1/admin/fila/falhas")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["itens"][0]["id"] == "job-1"
    assert "_bruto" not in corpo["itens"][0]


def test_reprocessar_fila_falha_inexistente_retorna_404(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _nao_encontrado(job_id: str) -> bool:
        return False

    monkeypatch.setattr("app.api.observabilidade.reprocessar_falha", _nao_encontrado)
    _sessao_tech()

    resposta = TestClient(app).post(
        "/v1/admin/fila/falhas/job-1/reprocessar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 404


def test_reprocessar_fila_falha_sucesso_audita_e_comita(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _encontrado(job_id: str) -> bool:
        return True

    monkeypatch.setattr("app.api.observabilidade.reprocessar_falha", _encontrado)
    session = _sessao_tech()

    resposta = TestClient(app).post(
        "/v1/admin/fila/falhas/job-1/reprocessar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 200
    assert resposta.json() == {"reprocessado": True}
    assert session.commits == 1


def test_descartar_fila_falha_sucesso(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _encontrado(job_id: str) -> bool:
        return True

    monkeypatch.setattr("app.api.observabilidade.descartar_falha", _encontrado)
    session = _sessao_tech()

    resposta = TestClient(app).delete(
        "/v1/admin/fila/falhas/job-1", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 200
    assert resposta.json() == {"descartado": True}
    assert session.commits == 1


# --- Achado de uma auditoria sistemática (08/09/2026, mesmo padrão do
# achado de EnvioCadenciaEmail): EventoOperacional so era exposto de forma
# agregada (contagens/medias) -- nenhum endpoint listava os eventos
# individuais para investigar um erro especifico. ---


def test_listar_eventos_operacionais_devolve_itens_formatados() -> None:
    evento = EventoOperacional(
        id=1,
        componente="api",
        operacao="POST /v1/admin/carteira",
        request_id="req-123",
        sucesso=False,
        duracao_ms=42,
        status_http=500,
        codigo_erro="ValueError",
        detalhes={"mensagem": "algo deu errado"},
        criado_em=datetime(2026, 9, 8, tzinfo=UTC),
    )
    _sessao_tech(FakeResult(scalar=1), FakeResult(itens=[evento]))

    resposta = TestClient(app).get("/v1/admin/observabilidade/eventos")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["itens"][0]["codigo_erro"] == "ValueError"
    assert corpo["itens"][0]["detalhes"] == {"mensagem": "algo deu errado"}


def test_listar_eventos_operacionais_exige_acesso_tech() -> None:
    session = FakeSession([])
    usuario = usuario_teste(perfil="operador")
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).get("/v1/admin/observabilidade/eventos")

    assert resposta.status_code == 403

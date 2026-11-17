from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import LembreteCRM
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

URL = "/v1/admin/crm/lembretes/concluir-em-lote"
HEADERS = {"X-CSRF-Token": "csrf-lote"}


def lembrete(id, status="pendente"):
    return LembreteCRM(id=id, organizacao_id=1, status=status)


@pytest.fixture
def cliente():
    def preparar(session, permissoes=None):
        usuario = usuario_teste("operador", {"crm.view", "crm.manage"} if permissoes is None else permissoes)
        object.__setattr__(usuario, "csrf_hash", hash_token("csrf-lote"))

        async def sessao():
            yield session

        app.dependency_overrides[get_session] = sessao
        app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
        return TestClient(app), usuario

    yield preparar
    app.dependency_overrides.clear()


def test_lote_conclui_audita_deduplica_e_trava_linhas(cliente):
    itens = [lembrete(1), lembrete(2)]
    session = FakeSession([FakeResult(itens=itens)])
    client, usuario = cliente(session)
    response = client.post(URL, json={"ids": [2, 1, 1]}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"concluidos": [1, 2], "ignorados": []}
    assert session.commits == 1
    assert all(item.status == "concluido" and item.concluido_por == usuario.ator for item in itens)
    assert itens[0].concluido_em == itens[1].concluido_em
    assert len(session.adicionados) == 2
    assert {evento.recurso for evento in session.adicionados} == {"lembrete_crm:1", "lembrete_crm:2"}
    assert all(evento.acao == "concluir_lote" and evento.organizacao_id == usuario.organizacao_id for evento in session.adicionados)
    query = session.executados[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    sql = str(query)
    assert "organizacao_id = 1" in sql
    assert "id IN (1, 2)" in sql
    assert "ORDER BY lembretes_crm.id FOR UPDATE OF lembretes_crm" in sql


def test_lote_preserva_concluidos_cancelados_e_repeticao_e_idempotente(cliente):
    itens = [lembrete(1), lembrete(2, "concluido"), lembrete(3, "cancelado")]
    original = datetime(2026, 11, 1, tzinfo=UTC)
    itens[1].concluido_em, itens[1].concluido_por = original, "Outro operador"
    session = FakeSession([FakeResult(itens=itens), FakeResult(itens=itens)])
    client, _ = cliente(session)
    assert client.post(URL, json={"ids": [1, 2, 3]}, headers=HEADERS).json() == {"concluidos": [1], "ignorados": [2, 3]}
    concluido_em = itens[0].concluido_em
    assert client.post(URL, json={"ids": [1, 2, 3]}, headers=HEADERS).json() == {"concluidos": [], "ignorados": [1, 2, 3]}
    assert itens[0].concluido_em == concluido_em
    assert itens[1].concluido_em == original and itens[1].concluido_por == "Outro operador"
    assert itens[2].status == "cancelado"
    assert len(session.adicionados) == 1


def test_lote_id_ausente_ou_de_outra_organizacao_nao_muda_nada(cliente):
    item = lembrete(1)
    session = FakeSession([FakeResult(itens=[item])])
    client, _ = cliente(session)
    response = client.post(URL, json={"ids": [1, 999]}, headers=HEADERS)
    assert response.status_code == 404
    assert item.status == "pendente" and session.commits == 0 and not session.adicionados


@pytest.mark.parametrize("ids", [[], [0], [-1], [True], [1.5], ["1"], list(range(1, 202))])
def test_lote_valida_ids_e_limite(cliente, ids):
    session = FakeSession()
    client, _ = cliente(session)
    assert client.post(URL, json={"ids": ids}, headers=HEADERS).status_code == 422
    assert session.commits == 0 and not session.executados


def test_lote_limite_de_200(cliente):
    session = FakeSession([FakeResult(itens=[lembrete(id) for id in range(1, 201)])])
    client, _ = cliente(session)
    response = client.post(URL, json={"ids": list(range(1, 201))}, headers=HEADERS)
    assert response.status_code == 200 and len(response.json()["concluidos"]) == 200
    assert session.commits == 1 and len(session.adicionados) == 200


def test_lote_exige_permissao(cliente):
    session = FakeSession()
    client, _ = cliente(session, {"crm.view"})
    assert client.post(URL, json={"ids": [1]}, headers=HEADERS).status_code == 403
    assert not session.executados


def test_lote_exige_csrf(cliente):
    session = FakeSession()
    client, _ = cliente(session)
    assert client.post(URL, json={"ids": [1]}).status_code == 403
    assert not session.executados


def test_lote_respeita_rate_limiter_compartilhado(cliente):
    from app.auth import _limitar_acoes

    session = FakeSession()
    client, usuario = cliente(session)
    for _ in range(60):
        _limitar_acoes.aplicar(f"usuario:{usuario.id}")
    response = client.post(URL, json={"ids": [1]}, headers=HEADERS)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert not session.executados


def test_lote_conflito_reverte_transacao(cliente):
    class SessaoComConflito(FakeSession):
        rollback_executado = False

        async def commit(self):
            raise IntegrityError("update", {}, Exception("conflito"))

        async def rollback(self):
            self.rollback_executado = True

    session = SessaoComConflito([FakeResult(itens=[lembrete(1), lembrete(2)])])
    client, _ = cliente(session)
    response = client.post(URL, json={"ids": [1, 2]}, headers=HEADERS)
    assert response.status_code == 409 and session.rollback_executado
    assert "Nenhum" in response.json()["detail"]

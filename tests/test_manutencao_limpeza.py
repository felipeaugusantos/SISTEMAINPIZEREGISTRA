import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.manutencao_limpeza import (
    CHAVE_ANDAMENTO,
    CHAVE_FILA,
    CHAVE_RELATORIO,
    CHAVE_RESULTADO,
    LimpezaJaSolicitada,
    ler_estado,
    solicitar_limpeza,
)
from app.models import EventoAuditoria
from tests.conftest import FakeSession, auth_override, usuario_teste


class FakeRedis:
    def __init__(self) -> None:
        self.dados: dict[str, str] = {}
        self.listas: dict[str, list[str]] = {}

    async def get(self, chave: str) -> str | None:
        return self.dados.get(chave)

    async def llen(self, chave: str) -> int:
        return len(self.listas.get(chave, []))

    async def exists(self, chave: str) -> int:
        return int(chave in self.dados)

    async def rpush(self, chave: str, valor: str) -> int:
        self.listas.setdefault(chave, []).append(valor)
        return len(self.listas[chave])

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def redis_falso(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    falso = FakeRedis()
    monkeypatch.setattr("app.manutencao_limpeza.cliente_redis", lambda: falso)
    return falso


def _superadmin():
    return replace(usuario_teste(), superadmin=True, csrf_hash=hash_token("tok"))


def _sessao(session: FakeSession):
    async def _gen():
        yield session

    return _gen


async def test_solicitar_enfileira_um_pedido_sem_comando(redis_falso: FakeRedis) -> None:
    pedido = await solicitar_limpeza("super@teste.local")
    fila = [json.loads(item) for item in redis_falso.listas[CHAVE_FILA]]
    assert len(fila) == 1
    assert set(fila[0]) == {"id", "solicitante", "solicitado_em"}
    assert fila[0]["id"] == pedido["id"]


async def test_segundo_pedido_e_recusado_enquanto_ha_pendente(redis_falso: FakeRedis) -> None:
    await solicitar_limpeza("a@teste.local")
    with pytest.raises(LimpezaJaSolicitada):
        await solicitar_limpeza("b@teste.local")


async def test_pedido_recusado_durante_limpeza_em_andamento(redis_falso: FakeRedis) -> None:
    redis_falso.dados[CHAVE_ANDAMENTO] = "1"
    with pytest.raises(LimpezaJaSolicitada):
        await solicitar_limpeza("a@teste.local")


async def test_ler_estado_junta_relatorio_resultado_e_situacao(redis_falso: FakeRedis) -> None:
    redis_falso.dados[CHAVE_RELATORIO] = json.dumps({"gerado_em": "2026-10-14T10:00:00Z", "itens": []})
    redis_falso.dados[CHAVE_RESULTADO] = json.dumps({"id": "x", "liberado_bytes": 5, "itens": []})
    await solicitar_limpeza("a@teste.local")
    estado = await ler_estado()
    assert estado["disponivel"] is True
    assert estado["relatorio"]["gerado_em"] == "2026-10-14T10:00:00Z"
    assert estado["resultado"]["liberado_bytes"] == 5
    assert estado["pendente"] is True
    assert estado["em_andamento"] is False


async def test_ler_estado_ignora_json_invalido(redis_falso: FakeRedis) -> None:
    redis_falso.dados[CHAVE_RELATORIO] = "{quebrado"
    estado = await ler_estado()
    assert estado["relatorio"] is None
    assert estado["resultado"] is None


def test_endpoint_de_estado_e_exclusivo_do_superadmin(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _estado() -> dict:
        return {"disponivel": True, "relatorio": None, "resultado": None, "pendente": False, "em_andamento": False}

    monkeypatch.setattr("app.api.observabilidade.ler_estado", _estado)
    cliente = TestClient(app)

    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())
    assert cliente.get("/v1/admin/observabilidade/limpeza").status_code == 403

    app.dependency_overrides[obter_usuario_atual] = auth_override(_superadmin())
    resposta = cliente.get("/v1/admin/observabilidade/limpeza")
    assert resposta.status_code == 200
    assert resposta.json()["disponivel"] is True


def test_solicitar_exige_superadmin_e_csrf(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[str] = []

    async def _solicitar(solicitante: str) -> dict:
        chamadas.append(solicitante)
        return {"id": "abc123", "solicitante": solicitante, "solicitado_em": "x"}

    monkeypatch.setattr("app.api.observabilidade.solicitar_limpeza", _solicitar)
    sessao = FakeSession([])
    app.dependency_overrides[get_session] = _sessao(sessao)
    cliente = TestClient(app)
    url = "/v1/admin/observabilidade/limpeza/solicitar"

    app.dependency_overrides[obter_usuario_atual] = auth_override(replace(usuario_teste(), csrf_hash=hash_token("tok")))
    assert cliente.post(url, headers={"X-CSRF-Token": "tok"}).status_code == 403

    app.dependency_overrides[obter_usuario_atual] = auth_override(_superadmin())
    assert cliente.post(url).status_code == 403  # sem token CSRF
    assert chamadas == []

    resposta = cliente.post(url, headers={"X-CSRF-Token": "tok"})
    assert resposta.status_code == 202
    assert resposta.json() == {"solicitado": True, "id": "abc123"}
    assert chamadas == ["admin@teste.local"]
    auditorias = [obj for obj in sessao.adicionados if isinstance(obj, EventoAuditoria)]
    assert len(auditorias) == 1
    assert auditorias[0].recurso == "limpeza_disco:abc123"


def test_solicitar_responde_409_quando_ja_ha_pedido(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _solicitar(_solicitante: str) -> dict:
        raise LimpezaJaSolicitada

    monkeypatch.setattr("app.api.observabilidade.solicitar_limpeza", _solicitar)
    app.dependency_overrides[get_session] = _sessao(FakeSession([]))
    app.dependency_overrides[obter_usuario_atual] = auth_override(_superadmin())
    resposta = TestClient(app).post("/v1/admin/observabilidade/limpeza/solicitar", headers={"X-CSRF-Token": "tok"})
    assert resposta.status_code == 409


def test_limite_de_pedidos_por_hora(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _solicitar(solicitante: str) -> dict:
        return {"id": "i", "solicitante": solicitante, "solicitado_em": "x"}

    monkeypatch.setattr("app.api.observabilidade.solicitar_limpeza", _solicitar)
    app.dependency_overrides[get_session] = _sessao(FakeSession([]))
    app.dependency_overrides[obter_usuario_atual] = auth_override(_superadmin())
    cliente = TestClient(app)
    url = "/v1/admin/observabilidade/limpeza/solicitar"
    codigos = [cliente.post(url, headers={"X-CSRF-Token": "tok"}).status_code for _ in range(4)]
    assert codigos == [202, 202, 202, 429]

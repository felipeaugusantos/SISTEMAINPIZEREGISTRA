import json
from types import SimpleNamespace

from app.queueing import calcular_atraso_retry, descartar_falha, enfileirar, listar_falhas, reprocessar_falha


def test_backoff_da_fila_e_exponencial_e_limitado() -> None:
    assert calcular_atraso_retry(1, 5, 60) == 5
    assert calcular_atraso_retry(2, 5, 60) == 10
    assert calcular_atraso_retry(5, 5, 60) == 60


async def test_enfileiramento_idempotente_nao_duplica_job(monkeypatch) -> None:
    class RedisFalso:
        def __init__(self):
            self.valores = {}
            self.jobs = []

        async def set(self, chave, valor, *, nx, ex):
            if nx and chave in self.valores:
                return False
            self.valores[chave] = valor
            return True

        async def get(self, chave):
            return self.valores.get(chave)

        async def rpush(self, _chave, valor):
            self.jobs.append(valor)

        async def hincrby(self, *_args):
            return 1

        async def aclose(self):
            return None

    redis = RedisFalso()
    monkeypatch.setattr("app.queueing.cliente_redis", lambda: redis)
    monkeypatch.setattr(
        "app.queueing.get_settings",
        lambda: SimpleNamespace(queue_idempotency_ttl_seconds=3600),
    )

    primeiro = await enfileirar("teste", {"id": 1}, idempotency_key="mesma-operacao")
    segundo = await enfileirar("teste", {"id": 1}, idempotency_key="mesma-operacao")

    assert len(redis.jobs) == 1
    assert segundo["duplicado"] is True
    assert segundo["id"] == primeiro["id"]


# --- Achado FASE6-4 da auditoria (04/09/2026): dead-letter queue visível e
# tratável -- antes só existia a contagem em status_fila(). ---


class _RedisFilaFalhas:
    """Fake mínimo de lista Redis para FAILED_KEY/QUEUE_KEY -- só o suficiente
    para exercitar lrange/lrem/rpush/hincrby, sem depender de Redis real."""

    def __init__(self, falhas: list[str]) -> None:
        self.listas: dict[str, list[str]] = {"ze-registra:jobs:failed": list(falhas), "ze-registra:jobs": []}
        self.metricas: dict[str, int] = {}

    async def lrange(self, chave: str, inicio: int, fim: int) -> list[str]:
        lista = self.listas.get(chave, [])
        fim = len(lista) if fim == -1 else fim + 1
        return lista[inicio:fim]

    async def lrem(self, chave: str, count: int, valor: str) -> int:
        lista = self.listas.setdefault(chave, [])
        if valor in lista:
            lista.remove(valor)
            return 1
        return 0

    async def rpush(self, chave: str, valor: str) -> None:
        self.listas.setdefault(chave, []).append(valor)

    async def hincrby(self, chave: str, campo: str, valor: int = 1) -> int:
        self.metricas[campo] = self.metricas.get(campo, 0) + valor
        return self.metricas[campo]

    async def aclose(self) -> None:
        return None


def _job_falho(**kwargs: object) -> str:
    base = {
        "id": "job-1",
        "tipo": "prospeccao.enriquecer_prospect",
        "payload": {"prospect_id": 5},
        "tentativas": 3,
        "ultimo_erro": "TimeoutError",
        "ultima_falha_em": "2026-09-04T10:00:00+00:00",
    }
    base.update(kwargs)
    return json.dumps(base)


def _tarefa_manutencao_falha(**kwargs: object) -> str:
    base = {"job": "juridico.executar_motor", "erro": "ValueError: x", "falhou_em": "2026-09-04T10:00:00+00:00"}
    base.update(kwargs)
    return json.dumps(base)


async def test_listar_falhas_devolve_jobs_da_fila(monkeypatch) -> None:
    redis = _RedisFilaFalhas([_job_falho(), _tarefa_manutencao_falha()])
    monkeypatch.setattr("app.queueing.cliente_redis", lambda: redis)

    itens = await listar_falhas()

    assert len(itens) == 2
    assert itens[0]["id"] == "job-1"
    assert itens[1]["job"] == "juridico.executar_motor"


async def test_reprocessar_falha_job_sob_demanda_reenfileira_com_tentativas_zeradas(monkeypatch) -> None:
    bruto = _job_falho()
    redis = _RedisFilaFalhas([bruto])
    monkeypatch.setattr("app.queueing.cliente_redis", lambda: redis)

    resultado = await reprocessar_falha("job-1")

    assert resultado is True
    assert redis.listas["ze-registra:jobs:failed"] == []
    reenfileirado = json.loads(redis.listas["ze-registra:jobs"][0])
    assert reenfileirado["tentativas"] == 0
    assert "ultimo_erro" not in reenfileirado
    assert reenfileirado["tipo"] == "prospeccao.enriquecer_prospect"


async def test_reprocessar_falha_tarefa_manutencao_usa_nome_do_job_como_tipo(monkeypatch) -> None:
    bruto = _tarefa_manutencao_falha()
    redis = _RedisFilaFalhas([bruto])
    monkeypatch.setattr("app.queueing.cliente_redis", lambda: redis)

    resultado = await reprocessar_falha("juridico.executar_motor")

    assert resultado is True
    reenfileirado = json.loads(redis.listas["ze-registra:jobs"][0])
    assert reenfileirado["tipo"] == "juridico.executar_motor"
    assert reenfileirado["payload"] == {}


async def test_reprocessar_falha_inexistente_devolve_false(monkeypatch) -> None:
    redis = _RedisFilaFalhas([])
    monkeypatch.setattr("app.queueing.cliente_redis", lambda: redis)

    assert await reprocessar_falha("nao-existe") is False


async def test_descartar_falha_remove_sem_reenfileirar(monkeypatch) -> None:
    bruto = _job_falho()
    redis = _RedisFilaFalhas([bruto])
    monkeypatch.setattr("app.queueing.cliente_redis", lambda: redis)

    resultado = await descartar_falha("job-1")

    assert resultado is True
    assert redis.listas["ze-registra:jobs:failed"] == []
    assert redis.listas["ze-registra:jobs"] == []

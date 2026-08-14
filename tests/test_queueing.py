from types import SimpleNamespace

from app.queueing import calcular_atraso_retry, enfileirar


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

import json
from types import SimpleNamespace

from app.prospeccao_reconciliacao import CARENCIA_SEGUNDOS, VISTAS_KEY, reconciliar_campanhas_ativas
from app.queueing import PROCESSING_KEY, QUEUE_KEY, RETRY_KEY
from app.worker import TAREFAS_MANUTENCAO_HORARIA
from tests.conftest import FakeResult, FakeSession

# Auditoria A06 (15/10/2026): campanha "ativa" sem job (processo morreu entre gravar o status e enfileirar).


class RedisFalso:
    def __init__(self, fila: list[str] | None = None, processando: list[str] | None = None) -> None:
        self.listas = {QUEUE_KEY: fila or [], PROCESSING_KEY: processando or []}
        self.retry: list[str] = []
        self.vistas: dict[str, str] = {}

    async def lrange(self, chave: str, _inicio: int, _fim: int) -> list[str]:
        return list(self.listas.get(chave, []))

    async def zrange(self, chave: str, _inicio: int, _fim: int) -> list[str]:
        assert chave == RETRY_KEY
        return list(self.retry)

    async def hgetall(self, chave: str) -> dict[str, str]:
        assert chave == VISTAS_KEY
        return dict(self.vistas)

    async def hset(self, _chave: str, campo: str, valor: str) -> None:
        self.vistas[campo] = valor

    async def hdel(self, _chave: str, campo: str) -> None:
        self.vistas.pop(campo, None)


def _campanha(campanha_id: int = 4) -> SimpleNamespace:
    return SimpleNamespace(id=campanha_id, status="ativa", encerrada_em=None)


def _job(campanha_id: int) -> str:
    return json.dumps({"id": "j", "tipo": "prospeccao.coletar_campanha", "payload": {"campanha_id": campanha_id}})


async def test_campanha_ativa_sem_job_so_e_pausada_na_segunda_varredura_apos_a_carencia() -> None:
    campanha = _campanha()
    redis = RedisFalso()

    primeira = await reconciliar_campanhas_ativas(FakeSession([FakeResult(itens=[campanha])]), redis, agora=1000.0)
    assert primeira["pausadas"] == [] and campanha.status == "ativa"
    assert "4" in redis.vistas

    cedo = await reconciliar_campanhas_ativas(
        FakeSession([FakeResult(itens=[campanha])]), redis, agora=1000.0 + CARENCIA_SEGUNDOS - 1
    )
    assert cedo["pausadas"] == [] and campanha.status == "ativa"

    sessao = FakeSession([FakeResult(itens=[campanha])])
    tarde = await reconciliar_campanhas_ativas(sessao, redis, agora=1000.0 + CARENCIA_SEGUNDOS)
    assert tarde["pausadas"] == [4]
    assert campanha.status == "pausada" and campanha.encerrada_em is not None
    assert sessao.commits == 1 and redis.vistas == {}


async def test_campanha_com_job_na_fila_em_processamento_ou_em_retry_nunca_e_tocada() -> None:
    campanhas = [_campanha(1), _campanha(2), _campanha(3)]
    redis = RedisFalso(fila=[_job(1)], processando=[_job(2)])
    redis.retry = [_job(3)]
    redis.vistas = {"1": "0", "2": "0", "3": "0"}  # vistas há muito tempo, mas agora têm job

    resultado = await reconciliar_campanhas_ativas(FakeSession([FakeResult(itens=campanhas)]), redis, agora=10_000.0)

    assert resultado["pausadas"] == []
    assert all(c.status == "ativa" for c in campanhas)
    assert redis.vistas == {}


async def test_registro_de_campanha_que_deixou_de_estar_ativa_e_esquecido() -> None:
    redis = RedisFalso()
    redis.vistas = {"9": "0"}

    await reconciliar_campanhas_ativas(FakeSession([FakeResult(itens=[])]), redis, agora=5000.0)

    assert redis.vistas == {}


def test_reconciliacao_esta_na_manutencao_horaria() -> None:
    assert "prospeccao.reconciliar_campanhas" in TAREFAS_MANUTENCAO_HORARIA

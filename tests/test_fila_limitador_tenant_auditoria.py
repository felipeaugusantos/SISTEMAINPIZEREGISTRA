import json
import os
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
import redis as redis_sync
import redis.asyncio as redis_async
from fastapi import HTTPException, Request

from app import ratelimit
from app.queueing import (
    mover_processando_para_falha,
    mover_processando_para_retry,
    recuperar_processando,
)
from app.ratelimit import RateLimiter
from app.tenancy import resolver_organizacao_publica
from tests.conftest import FakeResult, FakeSession

# Auditoria externa de 13/10/2026: A03 (tarefa perdida no worker), A06 (tenant aplicado tarde na chave
# de integração) e A08 (limitador Redis sem atomicidade). Os testes de Redis usam o servidor real
# (o mesmo do CI) com chaves únicas por teste e são ignorados quando ele não está disponível.

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def redis_real():
    cliente = redis_async.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await cliente.ping()
    except Exception as exc:  # noqa: BLE001 - qualquer falha de conexão -> sem Redis real
        await cliente.aclose()
        pytest.skip(f"Redis real indisponivel: {type(exc).__name__}")
    yield cliente
    await cliente.aclose()


@pytest.fixture
def chaves() -> SimpleNamespace:
    prefixo = f"teste:{uuid4().hex}:"
    return SimpleNamespace(
        processando=prefixo + "processando",
        retry=prefixo + "retry",
        falhas=prefixo + "falhas",
        metricas=prefixo + "metricas",
        fila=prefixo + "fila",
    )


async def _limpar(redis, chaves: SimpleNamespace) -> None:
    await redis.delete(*vars(chaves).values())


def _job(tentativas: int = 1) -> tuple[str, dict]:
    job = {"id": uuid4().hex, "tipo": "teste", "payload": {}, "tentativas": tentativas}
    return json.dumps({k: v for k, v in job.items() if k != "tentativas"}), job


async def test_a03_falha_move_para_retry_atomicamente(redis_real, chaves) -> None:
    bruto, job = _job()
    await redis_real.lpush(chaves.processando, bruto)
    resultado = await mover_processando_para_retry(
        redis_real, bruto, job, processando=chaves.processando, retry=chaves.retry, metricas=chaves.metricas
    )
    assert resultado == 1
    assert await redis_real.llen(chaves.processando) == 0
    assert await redis_real.zcard(chaves.retry) == 1
    assert await redis_real.hget(chaves.metricas, "retries") == "1"
    await _limpar(redis_real, chaves)


async def test_a03_destino_que_recusa_a_escrita_nao_perde_a_tarefa(redis_real, chaves) -> None:
    bruto, job = _job()
    await redis_real.lpush(chaves.processando, bruto)
    await redis_real.set(chaves.retry, "tipo-errado")  # ZADD nessa chave dá WRONGTYPE
    resultado = await mover_processando_para_retry(
        redis_real, bruto, job, processando=chaves.processando, retry=chaves.retry, metricas=chaves.metricas
    )
    assert resultado == -1
    assert await redis_real.lrange(chaves.processando, 0, -1) == [bruto]  # segue recuperável
    await _limpar(redis_real, chaves)


async def test_a03_esgotadas_as_tentativas_vai_para_a_dead_letter(redis_real, chaves) -> None:
    bruto, job = _job(tentativas=3)
    await redis_real.lpush(chaves.processando, bruto)
    resultado = await mover_processando_para_falha(
        redis_real, bruto, job, processando=chaves.processando, falhas=chaves.falhas, metricas=chaves.metricas
    )
    assert resultado == 1
    assert await redis_real.llen(chaves.processando) == 0
    assert await redis_real.llen(chaves.falhas) == 1
    assert await redis_real.hget(chaves.metricas, "falhas") == "1"
    await _limpar(redis_real, chaves)


async def test_a03_dead_letter_que_recusa_a_escrita_nao_perde_a_tarefa(redis_real, chaves) -> None:
    bruto, job = _job(tentativas=3)
    await redis_real.lpush(chaves.processando, bruto)
    await redis_real.set(chaves.falhas, "tipo-errado")  # RPUSH nessa chave dá WRONGTYPE
    resultado = await mover_processando_para_falha(
        redis_real, bruto, job, processando=chaves.processando, falhas=chaves.falhas, metricas=chaves.metricas
    )
    assert resultado == -1
    assert await redis_real.lrange(chaves.processando, 0, -1) == [bruto]
    await _limpar(redis_real, chaves)


async def test_a03_tarefa_que_ja_saiu_de_processamento_nao_e_duplicada(redis_real, chaves) -> None:
    bruto, job = _job()
    resultado = await mover_processando_para_retry(
        redis_real, bruto, job, processando=chaves.processando, retry=chaves.retry, metricas=chaves.metricas
    )
    assert resultado == 0
    assert await redis_real.zcard(chaves.retry) == 0
    await _limpar(redis_real, chaves)


async def test_a03_recuperacao_na_partida_devolve_todas_as_tarefas(redis_real, chaves) -> None:
    brutos = [_job()[0] for _ in range(3)]
    for bruto in brutos:
        await redis_real.lpush(chaves.processando, bruto)
    movidas = await recuperar_processando(redis_real, processando=chaves.processando, fila=chaves.fila)
    assert movidas == 3
    assert await redis_real.llen(chaves.processando) == 0
    assert sorted(await redis_real.lrange(chaves.fila, 0, -1)) == sorted(brutos)
    await _limpar(redis_real, chaves)


def test_a08_limitador_redis_respeita_o_teto_com_chamadas_simultaneas(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = redis_sync.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        cliente.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis real indisponivel: {type(exc).__name__}")
    monkeypatch.setattr(
        ratelimit, "get_settings", lambda: SimpleNamespace(ratelimit_redis_enabled=True, redis_url=REDIS_URL)
    )
    monkeypatch.setattr(ratelimit, "_cliente_redis", None)
    monkeypatch.setattr(ratelimit, "_redis_indisponivel_ate", 0.0)

    escopo = f"teste-{uuid4().hex}"
    limitador = RateLimiter(limite=5, janela_segundos=30, escopo=escopo)
    tentativas = 40
    barreira = threading.Barrier(tentativas)
    admitidas: list[int] = []
    recusadas: list[int] = []

    def chamar() -> None:
        barreira.wait()
        try:
            limitador.aplicar("ip")
            admitidas.append(1)
        except HTTPException as erro:
            assert erro.status_code == 429
            recusadas.append(1)

    threads = [threading.Thread(target=chamar) for _ in range(tentativas)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    chave = f"ratelimit:{escopo}:ip"
    try:
        # Garante que o caminho Redis foi o usado (e não o fallback em memória).
        assert cliente.zcard(chave) == 5
        assert len(admitidas) == 5
        assert len(recusadas) == tentativas - 5
    finally:
        cliente.delete(chave)


def test_a08_recusa_informa_quanto_esperar(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = redis_sync.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        cliente.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis real indisponivel: {type(exc).__name__}")
    monkeypatch.setattr(
        ratelimit, "get_settings", lambda: SimpleNamespace(ratelimit_redis_enabled=True, redis_url=REDIS_URL)
    )
    monkeypatch.setattr(ratelimit, "_cliente_redis", None)
    monkeypatch.setattr(ratelimit, "_redis_indisponivel_ate", 0.0)
    escopo = f"teste-{uuid4().hex}"
    limitador = RateLimiter(limite=1, janela_segundos=30, escopo=escopo)
    try:
        limitador.aplicar("ip")
        with pytest.raises(HTTPException) as erro:
            limitador.aplicar("ip")
        assert erro.value.status_code == 429
        assert 1 <= int(erro.value.headers["Retry-After"]) <= 31
    finally:
        cliente.delete(f"ratelimit:{escopo}:ip")


class _Credencial:
    """Registra quando ultimo_uso_em é gravado, para conferir a ordem das operações."""

    def __init__(self, ordem: list[str]) -> None:
        object.__setattr__(self, "_ordem", ordem)
        self.organizacao_id = 5
        self.expira_em = None

    def __setattr__(self, nome: str, valor: object) -> None:
        if nome == "ultimo_uso_em":
            self._ordem.append("uso")
        object.__setattr__(self, nome, valor)


async def test_a06_tenant_e_aplicado_antes_de_marcar_o_uso_da_chave(monkeypatch: pytest.MonkeyPatch) -> None:
    ordem: list[str] = []

    async def registrar_tenant(_session, organizacao_id: int, superadmin: bool = False) -> None:
        ordem.append("tenant")

    organizacao = SimpleNamespace(
        id=5,
        nome="Escritório Teste",
        slug="escritorio-teste",
        status="ativa",
        plano=SimpleNamespace(codigo="profissional", modulos=["consulta"], limites={}),
        modulos_liberados=None,
        branding={},
        politica_privacidade_versao=None,
    )
    session = FakeSession([FakeResult(scalar=_Credencial(ordem)), FakeResult(scalar=organizacao)])
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", b"api.cliente.test"), (b"x-integration-key", b"chave-saas")],
            "server": ("api.cliente.test", 443),
            "client": ("127.0.0.1", 12345),
        }
    )
    monkeypatch.setattr("app.tenancy.aplicar_contexto_tenant", registrar_tenant)
    monkeypatch.setattr(
        "app.tenancy.get_settings",
        lambda: SimpleNamespace(
            integration_auth_enabled=False,
            inpi_integration_token="",
            app_env="production",
            app_public_url="https://app.zeregistra.com.br",
            default_organization_slug="ze-registra",
        ),
    )

    atual = await resolver_organizacao_publica(request, session)

    assert atual.id == 5
    assert "uso" in ordem
    assert ordem.index("tenant") < ordem.index("uso")

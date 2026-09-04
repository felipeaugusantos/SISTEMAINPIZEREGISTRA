import json
import time
from datetime import UTC, datetime
from uuid import uuid4

from redis.asyncio import Redis

from app.request_context import request_id_atual
from app.settings import get_settings

QUEUE_KEY = "ze-registra:jobs"
FAILED_KEY = "ze-registra:jobs:failed"
PROCESSING_KEY = "ze-registra:jobs:processing"
RETRY_KEY = "ze-registra:jobs:retry"
METRICS_KEY = "ze-registra:jobs:metrics"
IDEMPOTENCY_PREFIX = "ze-registra:jobs:idempotency:"
MAX_ATTEMPTS = 3


def cliente_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def calcular_atraso_retry(tentativa: int, base: int, maximo: int) -> int:
    return min(maximo, base * (2 ** max(0, tentativa - 1)))


async def enfileirar(
    tipo: str,
    payload: dict | None = None,
    *,
    idempotency_key: str | None = None,
    request_id: str | None = None,
) -> dict:
    settings = get_settings()
    job = {
        "id": str(uuid4()),
        "tipo": tipo,
        "payload": payload or {},
        "request_id": request_id or request_id_atual() or str(uuid4()),
        "idempotency_key": idempotency_key,
        "tentativas": 0,
        "criado_em": datetime.now(UTC).isoformat(),
    }
    redis = cliente_redis()
    try:
        if idempotency_key:
            chave = f"{IDEMPOTENCY_PREFIX}{tipo}:{idempotency_key}"
            criado = await redis.set(
                chave,
                job["id"],
                nx=True,
                ex=settings.queue_idempotency_ttl_seconds,
            )
            if not criado:
                return {
                    "id": await redis.get(chave),
                    "tipo": tipo,
                    "duplicado": True,
                    "request_id": job["request_id"],
                }
        await redis.rpush(QUEUE_KEY, json.dumps(job))
        await redis.hincrby(METRICS_KEY, "enfileirados", 1)
    finally:
        await redis.aclose()
    return job


async def agendar_retry(redis: Redis, job: dict) -> int:
    settings = get_settings()
    atraso = calcular_atraso_retry(
        int(job.get("tentativas", 1)),
        settings.queue_retry_base_seconds,
        settings.queue_retry_max_seconds,
    )
    job["proxima_tentativa_em"] = datetime.fromtimestamp(time.time() + atraso, UTC).isoformat()
    await redis.zadd(RETRY_KEY, {json.dumps(job): time.time() + atraso})
    await redis.hincrby(METRICS_KEY, "retries", 1)
    return atraso


async def promover_retentativas(redis: Redis, agora: float | None = None) -> int:
    vencidos = await redis.zrangebyscore(RETRY_KEY, "-inf", time.time() if agora is None else agora)
    promovidos = 0
    for bruto in vencidos:
        if await redis.zrem(RETRY_KEY, bruto):
            await redis.rpush(QUEUE_KEY, bruto)
            promovidos += 1
    return promovidos


async def listar_falhas(*, limite: int = 50, deslocamento: int = 0) -> list[dict]:
    """Achado FASE6-4 da auditoria (04/09/2026): FAILED_KEY (dead-letter
    queue) só era exposta como contagem (status_fila) -- ninguém conseguia
    ver O QUE falhou nem reprocessar/descartar sem acessar redis-cli
    diretamente. Devolve os jobs mais recentes primeiro (LPUSH empurra no
    início, então lrange do começo já é do mais novo pro mais velho)."""
    redis = cliente_redis()
    try:
        brutos = await redis.lrange(FAILED_KEY, deslocamento, deslocamento + limite - 1)
    finally:
        await redis.aclose()
    itens = []
    for bruto in brutos:
        try:
            job = json.loads(bruto)
        except (TypeError, json.JSONDecodeError):
            job = {"job": None, "erro": "payload_invalido"}
        job["_bruto"] = bruto
        itens.append(job)
    return itens


async def reprocessar_falha(job_id: str) -> bool:
    """Remove UM job da fila de falhas (por id, seja 'id' de job sob demanda
    ou 'job' de tarefa de manutenção) e o reenvia para QUEUE_KEY com o
    contador de tentativas zerado. Devolve False se não achou o job (já
    reprocessado/removido por outra chamada)."""
    redis = cliente_redis()
    try:
        brutos = await redis.lrange(FAILED_KEY, 0, -1)
        for bruto in brutos:
            try:
                job = json.loads(bruto)
            except (TypeError, json.JSONDecodeError):
                continue
            if job.get("id") == job_id or job.get("job") == job_id:
                removido = await redis.lrem(FAILED_KEY, 1, bruto)
                if not removido:
                    return False
                job["tentativas"] = 0
                job.pop("ultimo_erro", None)
                job.pop("ultima_falha_em", None)
                if "id" not in job:
                    job["id"] = str(uuid4())
                if "tipo" not in job and "job" in job:
                    job["tipo"] = job.pop("job")
                job.setdefault("payload", {})
                await redis.rpush(QUEUE_KEY, json.dumps(job))
                await redis.hincrby(METRICS_KEY, "reenfileirados_apos_falha", 1)
                return True
        return False
    finally:
        await redis.aclose()


async def descartar_falha(job_id: str) -> bool:
    """Remove UM job da fila de falhas permanentemente, sem reprocessar."""
    redis = cliente_redis()
    try:
        brutos = await redis.lrange(FAILED_KEY, 0, -1)
        for bruto in brutos:
            try:
                job = json.loads(bruto)
            except (TypeError, json.JSONDecodeError):
                continue
            if job.get("id") == job_id or job.get("job") == job_id:
                return bool(await redis.lrem(FAILED_KEY, 1, bruto))
        return False
    finally:
        await redis.aclose()


async def status_fila() -> dict:
    redis = cliente_redis()
    try:
        await redis.ping()
        return {
            "status": "ok",
            "pendentes": await redis.llen(QUEUE_KEY),
            "falhas": await redis.llen(FAILED_KEY),
            "processando": await redis.llen(PROCESSING_KEY),
            "retries_aguardando": await redis.zcard(RETRY_KEY),
            "metricas": {chave: int(valor) for chave, valor in (await redis.hgetall(METRICS_KEY)).items()},
        }
    except Exception as exc:
        return {
            "status": "indisponivel",
            "erro": type(exc).__name__,
            "pendentes": None,
            "falhas": None,
            "processando": None,
            "retries_aguardando": None,
            "metricas": {},
        }
    finally:
        await redis.aclose()

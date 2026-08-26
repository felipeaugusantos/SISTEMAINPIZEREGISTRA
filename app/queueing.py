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

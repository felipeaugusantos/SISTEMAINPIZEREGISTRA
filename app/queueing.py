import json
from datetime import UTC, datetime
from uuid import uuid4

from redis.asyncio import Redis

from app.settings import get_settings

QUEUE_KEY = "ze-registra:jobs"
FAILED_KEY = "ze-registra:jobs:failed"
PROCESSING_KEY = "ze-registra:jobs:processing"
MAX_ATTEMPTS = 3


def cliente_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


async def enfileirar(tipo: str, payload: dict | None = None) -> dict:
    job = {
        "id": str(uuid4()),
        "tipo": tipo,
        "payload": payload or {},
        "tentativas": 0,
        "criado_em": datetime.now(UTC).isoformat(),
    }
    redis = cliente_redis()
    try:
        await redis.rpush(QUEUE_KEY, json.dumps(job))
    finally:
        await redis.aclose()
    return job


async def status_fila() -> dict:
    redis = cliente_redis()
    try:
        await redis.ping()
        return {
            "status": "ok",
            "pendentes": await redis.llen(QUEUE_KEY),
            "falhas": await redis.llen(FAILED_KEY),
            "processando": await redis.llen(PROCESSING_KEY),
        }
    except Exception as exc:
        return {
            "status": "indisponivel",
            "erro": type(exc).__name__,
            "pendentes": None,
            "falhas": None,
        }
    finally:
        await redis.aclose()

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


# Achado 2 da auditoria: promover retentativas fazia zrem e depois rpush em
# passos separados -- uma interrupção entre os dois perdia a tarefa. O script
# move cada vencido (rpush na fila + zrem da retry) atomicamente. Limitado a um
# lote por chamada (ARGV[2]) para não bloquear o Redis com um backlog enorme
# (revisão do Codex, PR #171); o que sobrar é promovido na próxima passagem.
_LUA_PROMOVER_RETRY = """
local vencidos = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
for _, m in ipairs(vencidos) do
  redis.call('RPUSH', KEYS[2], m)
  redis.call('ZREM', KEYS[1], m)
end
return #vencidos
"""
PROMOVER_RETRY_LOTE = 500

# Achado 2 da auditoria: reprocessar uma falha fazia lrem e depois rpush em
# passos separados. Remove da dead-letter e reenfileira atomicamente; se o
# rpush levantar erro (ex.: tipo errado na chave), restaura a entrada na
# dead-letter para não perder o job (revisão do Codex, PR #171).
_LUA_REPROCESSAR_FALHA = """
if redis.call('LREM', KEYS[1], 1, ARGV[1]) == 1 then
  local ok = pcall(function() redis.call('RPUSH', KEYS[2], ARGV[2]) end)
  if not ok then
    redis.call('LPUSH', KEYS[1], ARGV[1])
    return -1
  end
  return 1
end
return 0
"""


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
    chave = f"{IDEMPOTENCY_PREFIX}{tipo}:{idempotency_key}" if idempotency_key else None
    try:
        if chave is not None:
            criado = await redis.set(chave, job["id"], nx=True, ex=settings.queue_idempotency_ttl_seconds)
            if not criado:
                return {
                    "id": await redis.get(chave),
                    "tipo": tipo,
                    "duplicado": True,
                    "request_id": job["request_id"],
                }
        try:
            await redis.rpush(QUEUE_KEY, json.dumps(job))
        except Exception:
            # Achado 3 da auditoria: se o enfileiramento falhasse depois de
            # gravar a chave de dedup, a chave ficava no Redis e bloqueava a
            # nova tentativa para sempre (tratada como duplicada). Libera a
            # chave antes de propagar o erro.
            if chave is not None:
                await redis.delete(chave)
            raise
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
    # Move os vencidos da retry para a fila atomicamente, em lotes, até esvaziar
    # os vencidos -- sem bloquear o Redis com um backlog enorme de uma vez.
    limite = time.time() if agora is None else agora
    total = 0
    while True:
        movidos = int(
            await redis.eval(_LUA_PROMOVER_RETRY, 2, RETRY_KEY, QUEUE_KEY, str(limite), str(PROMOVER_RETRY_LOTE))
        )
        total += movidos
        if movidos < PROMOVER_RETRY_LOTE:
            break
    return total


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
                job["tentativas"] = 0
                job.pop("ultimo_erro", None)
                job.pop("ultima_falha_em", None)
                if "id" not in job:
                    job["id"] = str(uuid4())
                if "tipo" not in job and "job" in job:
                    job["tipo"] = job.pop("job")
                job.setdefault("payload", {})
                # Remove da dead-letter e reenfileira atomicamente (achado 2).
                # Retorno: 1 = movido, 0 = não estava mais lá, -1 = rpush falhou
                # e a entrada foi restaurada na dead-letter (nada perdido).
                movido = int(
                    await redis.eval(_LUA_REPROCESSAR_FALHA, 2, FAILED_KEY, QUEUE_KEY, bruto, json.dumps(job))
                )
                if movido != 1:
                    return False
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

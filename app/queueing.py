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


# Achado 3 da auditoria de filas (07/10/2026): a chave de deduplicação era
# gravada ANTES do rpush. Se o rpush falhasse, a chave ficava no Redis e a
# nova tentativa era tratada como duplicada -- a tarefa nunca entrava na fila.
# Este script faz o SET NX e o RPUSH juntos, atomicamente: ou grava a chave E
# enfileira, ou nenhum dos dois.
_LUA_ENFILEIRAR_DEDUP = """
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
  redis.call('RPUSH', KEYS[2], ARGV[3])
  return 1
end
return 0
"""

# Achado 2 da auditoria: promover retentativas fazia zrem e depois rpush em
# passos separados -- uma interrupção entre os dois perdia a tarefa. O script
# move cada vencido (rpush na fila + zrem da retry) atomicamente.
_LUA_PROMOVER_RETRY = """
local vencidos = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
for _, m in ipairs(vencidos) do
  redis.call('RPUSH', KEYS[2], m)
  redis.call('ZREM', KEYS[1], m)
end
return #vencidos
"""

# Achado 2 da auditoria: reprocessar uma falha fazia lrem e depois rpush em
# passos separados. O script remove da dead-letter e reenfileira atomicamente.
_LUA_REPROCESSAR_FALHA = """
if redis.call('LREM', KEYS[1], 1, ARGV[1]) == 1 then
  redis.call('RPUSH', KEYS[2], ARGV[2])
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
    try:
        if idempotency_key:
            chave = f"{IDEMPOTENCY_PREFIX}{tipo}:{idempotency_key}"
            # Dedup e enfileiramento atômicos (achado 2/3 da auditoria): sem
            # janela entre gravar a chave e entrar na fila.
            criado = await redis.eval(
                _LUA_ENFILEIRAR_DEDUP,
                2,
                chave,
                QUEUE_KEY,
                job["id"],
                str(settings.queue_idempotency_ttl_seconds),
                json.dumps(job),
            )
            if not criado:
                return {
                    "id": await redis.get(chave),
                    "tipo": tipo,
                    "duplicado": True,
                    "request_id": job["request_id"],
                }
        else:
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
    # Move os vencidos da retry para a fila atomicamente (achado 2 da auditoria).
    return int(
        await redis.eval(_LUA_PROMOVER_RETRY, 2, RETRY_KEY, QUEUE_KEY, str(time.time() if agora is None else agora))
    )


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
                movido = await redis.eval(_LUA_REPROCESSAR_FALHA, 2, FAILED_KEY, QUEUE_KEY, bruto, json.dumps(job))
                if not movido:
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

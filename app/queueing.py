import hashlib
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

# Auditoria externa A03: no tratamento de erro do worker a tarefa saía da lista de processamento (LREM)
# ANTES de ser gravada em retry ou na dead-letter; se a gravação falhasse (Redis sem memória, tipo
# errado, queda) a tarefa sumia. Estes scripts movem origem -> destino de forma atômica e, se o destino
# recusar a escrita (pcall), devolvem a tarefa à origem e sinalizam -1: ela segue recuperável.
_LUA_PROCESSANDO_PARA_RETRY = """
if redis.call('LREM', KEYS[1], 1, ARGV[1]) == 1 then
  -- Só o ZADD (um único comando: se falhar, nada foi gravado) decide se a tarefa volta à origem.
  -- A métrica fica fora desse bloco: o Redis não desfaz comandos já executados, então uma falha
  -- nela depois do ZADD deixaria a tarefa duplicada (em retry E em processamento).
  local ok = pcall(function()
    redis.call('ZADD', KEYS[2], tonumber(ARGV[3]), ARGV[2])
  end)
  if not ok then
    redis.call('LPUSH', KEYS[1], ARGV[1])
    return -1
  end
  pcall(function() redis.call('HINCRBY', KEYS[3], 'retries', 1) end)
  return 1
end
return 0
"""
_LUA_PROCESSANDO_PARA_FALHA = """
if redis.call('LREM', KEYS[1], 1, ARGV[1]) == 1 then
  local ok = pcall(function()
    redis.call('RPUSH', KEYS[2], ARGV[2])
  end)
  if not ok then
    redis.call('LPUSH', KEYS[1], ARGV[1])
    return -1
  end
  pcall(function() redis.call('HINCRBY', KEYS[3], 'falhas', 1) end)
  return 1
end
return 0
"""


# Auditoria A03: reserva de idempotência + RPUSH em uma única operação atômica. Retorna {1, id} quando
# enfileirou, {0, id_existente} quando a chave já estava reservada e {-1} quando o RPUSH foi recusado
# (a reserva é desfeita para não bloquear a nova tentativa). A métrica fica fora da decisão (pcall).
_LUA_ENFILEIRAR = """
if ARGV[1] == '1' then
  if not redis.call('SET', KEYS[1], ARGV[2], 'NX', 'EX', tonumber(ARGV[3])) then
    return {0, redis.call('GET', KEYS[1])}
  end
end
local ok = pcall(function() redis.call('RPUSH', KEYS[2], ARGV[4]) end)
if not ok then
  if ARGV[1] == '1' then redis.call('DEL', KEYS[1]) end
  return {-1}
end
pcall(function() redis.call('HINCRBY', KEYS[3], 'enfileirados', 1) end)
return {1, ARGV[2]}
"""


async def liberar_idempotencia(redis: Redis, tipo: str, idempotency_key: str) -> None:
    """Libera a reserva de idempotência de um job que foi para a dead-letter.

    Achado da auditoria de 2026-10-27: a chave ficava retida pelo TTL inteiro
    (24h) mesmo depois da falha definitiva, então o endpoint devolvia
    "duplicado" para todo novo clique do usuário e a ação nunca mais rodava
    naquele dia. Só depois da falha definitiva é seguro liberar: enquanto o
    job está na fila, em execução ou em retry, a reserva continua válida.
    """
    if not idempotency_key:
        return
    await redis.delete(f"{IDEMPOTENCY_PREFIX}{tipo}:{idempotency_key}")


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
    chave = f"{IDEMPOTENCY_PREFIX}{tipo}:{idempotency_key}" if idempotency_key else IDEMPOTENCY_PREFIX + "-"
    try:
        # Auditoria A03 (15/10/2026): a reserva de idempotência (SET NX) e o RPUSH eram comandos separados;
        # uma queda entre eles deixava a chave reservada sem trabalho na fila. O script faz tudo atômico.
        resultado = await redis.eval(
            _LUA_ENFILEIRAR,
            3,
            chave,
            QUEUE_KEY,
            METRICS_KEY,
            "1" if idempotency_key else "0",
            job["id"],
            str(settings.queue_idempotency_ttl_seconds),
            json.dumps(job),
        )
        if int(resultado[0]) == -1:
            raise RuntimeError("Redis recusou o enfileiramento do trabalho")
        if int(resultado[0]) == 0:
            return {
                "id": resultado[1],
                "tipo": tipo,
                "duplicado": True,
                "request_id": job["request_id"],
            }
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


async def mover_processando_para_retry(
    redis: Redis,
    bruto_original: str,
    job: dict,
    *,
    processando: str = PROCESSING_KEY,
    retry: str = RETRY_KEY,
    metricas: str = METRICS_KEY,
) -> int:
    """Move a tarefa de "em processamento" para a fila de retry atomicamente.

    Devolve 1 se moveu, 0 se a tarefa já não estava em processamento (não duplica) e -1 se o
    destino recusou a escrita -- nesse caso ela volta a "em processamento" (recuperável).
    """
    settings = get_settings()
    atraso = calcular_atraso_retry(
        int(job.get("tentativas", 1)), settings.queue_retry_base_seconds, settings.queue_retry_max_seconds
    )
    quando = time.time() + atraso
    job["proxima_tentativa_em"] = datetime.fromtimestamp(quando, UTC).isoformat()
    return int(
        await redis.eval(
            _LUA_PROCESSANDO_PARA_RETRY, 3, processando, retry, metricas, bruto_original, json.dumps(job), str(quando)
        )
    )


async def mover_processando_para_falha(
    redis: Redis,
    bruto_original: str,
    job: dict,
    *,
    processando: str = PROCESSING_KEY,
    falhas: str = FAILED_KEY,
    metricas: str = METRICS_KEY,
) -> int:
    """Move a tarefa de "em processamento" para a dead-letter atomicamente (mesmos retornos acima)."""
    return int(
        await redis.eval(_LUA_PROCESSANDO_PARA_FALHA, 3, processando, falhas, metricas, bruto_original, json.dumps(job))
    )


# Auditoria A05 (15/10/2026): iniciar um worker devolvia à fila TODOS os itens em processamento, inclusive
# os de outro worker ainda vivo (execução duplicada). Agora cada tarefa em andamento tem um lease
# (hash field = sha1 do JSON -> expiração em epoch) renovado pelo worker dono. A varredura só devolve à
# fila o que está com lease vencido; item sem lease (ex.: acabou de ser pego, ou legado) ganha um lease
# novo e só é recuperado se esse prazo também vencer.
LEASES_KEY = "ze-registra:jobs:leases"
LEASE_TTL_SECONDS = 60
LEASE_RENOVACAO_SECONDS = 15
_LUA_RECUPERAR_VENCIDOS = """
local itens = redis.call('LRANGE', KEYS[1], 0, -1)
local agora = tonumber(ARGV[1])
local movidas = 0
for _, item in ipairs(itens) do
  local campo = redis.sha1hex(item)
  local expira = redis.call('HGET', KEYS[3], campo)
  if not expira then
    redis.call('HSET', KEYS[3], campo, agora + tonumber(ARGV[2]))
  elseif tonumber(expira) <= agora then
    if redis.call('LREM', KEYS[1], 1, item) == 1 then
      redis.call('LPUSH', KEYS[2], item)
      movidas = movidas + 1
    end
    redis.call('HDEL', KEYS[3], campo)
  end
end
return movidas
"""


def _campo_lease(bruto: str) -> str:
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()  # noqa: S324 - só identifica o item, igual ao redis.sha1hex


async def registrar_lease(
    redis: Redis, bruto: str, *, leases: str = LEASES_KEY, ttl: int = LEASE_TTL_SECONDS, agora: float | None = None
) -> None:
    """Cria ou renova o lease da tarefa em processamento (chamado pelo worker dono)."""
    await redis.hset(leases, _campo_lease(bruto), str((time.time() if agora is None else agora) + ttl))


async def liberar_lease(redis: Redis, bruto: str, *, leases: str = LEASES_KEY) -> None:
    await redis.hdel(leases, _campo_lease(bruto))


async def recuperar_processando(
    redis: Redis,
    *,
    processando: str = PROCESSING_KEY,
    fila: str = QUEUE_KEY,
    leases: str = LEASES_KEY,
    ttl: int = LEASE_TTL_SECONDS,
    agora: float | None = None,
) -> int:
    """Devolve à fila as tarefas em processamento cujo lease venceu (worker morto), sem tocar nas que
    outro worker ainda renova. Atômico no Redis (script), então não há janela entre remover e reenfileirar."""
    return int(
        await redis.eval(
            _LUA_RECUPERAR_VENCIDOS,
            3,
            processando,
            fila,
            leases,
            str(time.time() if agora is None else agora),
            str(ttl),
        )
    )


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

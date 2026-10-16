"""Reconciliação de campanhas de prospecção "ativas" sem tarefa na fila (auditoria A06, 15/10/2026).

Ao disparar a coleta, o status "ativa" é gravado ANTES de enfileirar o job (ver
app/api/prospeccao.py::coletar_campanha). Se o processo morrer entre as duas etapas, a campanha fica
"ativa" sem nenhuma tarefa que a conclua, e a tela bloqueia novos disparos ("já está sendo coletada").

A rotina observa as campanhas ativas e procura o job de coleta de cada uma na fila, em processamento e
nas retentativas. Quem aparece sem job em duas varreduras separadas por pelo menos
``CARENCIA_SEGUNDOS`` volta para "pausada", que permite coletar de novo. Campanha com job em andamento
nunca é tocada.
"""

import json
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CampanhaProspeccao
from app.queueing import PROCESSING_KEY, QUEUE_KEY, RETRY_KEY

logger = logging.getLogger("ze_registra.prospeccao")

TIPO_COLETA = "prospeccao.coletar_campanha"
VISTAS_KEY = "ze-registra:prospeccao:campanhas-ativas-sem-job"
CARENCIA_SEGUNDOS = 1800


def _campanha_do_job(bruto: str) -> int | None:
    try:
        job = json.loads(bruto)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(job, dict) or job.get("tipo") != TIPO_COLETA:
        return None
    campanha_id = (job.get("payload") or {}).get("campanha_id")
    return int(campanha_id) if isinstance(campanha_id, int) else None


async def campanhas_com_job(redis) -> set[int]:
    """Ids de campanhas que têm job de coleta aguardando, em andamento ou em retentativa."""
    brutos = list(await redis.lrange(QUEUE_KEY, 0, -1)) + list(await redis.lrange(PROCESSING_KEY, 0, -1))
    brutos += list(await redis.zrange(RETRY_KEY, 0, -1))
    return {campanha_id for bruto in brutos if (campanha_id := _campanha_do_job(bruto)) is not None}


async def reconciliar_campanhas_ativas(session: AsyncSession, redis, *, agora: float | None = None) -> dict:
    momento = time.time() if agora is None else agora
    ativas = list(
        (await session.execute(select(CampanhaProspeccao).where(CampanhaProspeccao.status == "ativa"))).scalars()
    )
    com_job = await campanhas_com_job(redis)
    vistas = {int(k): float(v) for k, v in (await redis.hgetall(VISTAS_KEY)).items()}
    ids_ativos = {campanha.id for campanha in ativas}

    # Esquece o que não está mais ativo ou voltou a ter job.
    for campanha_id in list(vistas):
        if campanha_id not in ids_ativos or campanha_id in com_job:
            await redis.hdel(VISTAS_KEY, str(campanha_id))
            vistas.pop(campanha_id)

    pausadas: list[int] = []
    for campanha in ativas:
        if campanha.id in com_job:
            continue
        primeira_vez = vistas.get(campanha.id)
        if primeira_vez is None:
            await redis.hset(VISTAS_KEY, str(campanha.id), str(momento))
        elif momento - primeira_vez >= CARENCIA_SEGUNDOS:
            campanha.status = "pausada"
            campanha.encerrada_em = datetime.now(UTC)
            await redis.hdel(VISTAS_KEY, str(campanha.id))
            pausadas.append(campanha.id)
            logger.warning(
                json.dumps(
                    {"event": "CAMPANHA_ATIVA_SEM_JOB_PAUSADA", "campanha_id": campanha.id}, ensure_ascii=False
                )
            )
    if pausadas:
        await session.commit()
    return {"ativas": len(ativas), "pausadas": pausadas}

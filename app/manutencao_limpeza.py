"""Limpeza de disco pedida pelo painel do superadmin.

O painel NUNCA executa a limpeza: a API não tem (e não deve ter) acesso ao socket
do Docker, que equivale a root na VPS. Ela só grava um pedido na fila Redis; o script
``docker/manutencao-limpeza.sh`` (cron do host, a cada 5 minutos) consome o pedido,
executa uma lista FIXA de limpezas e publica o resultado. O pedido não carrega
comando nenhum -- o script ignora o conteúdo e só usa o fato de existir.

Chaves Redis (mantidas em sincronia com o script):
- ``manutencao:limpeza:fila``       lista de pedidos (JSON)
- ``manutencao:limpeza:relatorio``  quanto cada categoria ocupa (publicado pelo script)
- ``manutencao:limpeza:resultado``  resultado da última limpeza
- ``manutencao:limpeza:andamento``  marcador enquanto o script executa
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from app.queueing import cliente_redis

logger = logging.getLogger("ze_registra.manutencao_limpeza")

CHAVE_FILA = "manutencao:limpeza:fila"
CHAVE_RELATORIO = "manutencao:limpeza:relatorio"
CHAVE_RESULTADO = "manutencao:limpeza:resultado"
CHAVE_ANDAMENTO = "manutencao:limpeza:andamento"
MAXIMO_PEDIDOS_PENDENTES = 1


class LimpezaJaSolicitada(Exception):
    """Já existe um pedido na fila ou uma limpeza em andamento."""


def _decodificar(bruto: str | None) -> dict | None:
    if not bruto:
        return None
    try:
        valor = json.loads(bruto)
    except ValueError:
        return None
    return valor if isinstance(valor, dict) else None


async def ler_estado() -> dict:
    """Relatório, último resultado e situação do pedido, lidos do Redis."""
    redis = cliente_redis()
    try:
        relatorio = await redis.get(CHAVE_RELATORIO)
        resultado = await redis.get(CHAVE_RESULTADO)
        pendentes = await redis.llen(CHAVE_FILA)
        andamento = await redis.exists(CHAVE_ANDAMENTO)
    except Exception:
        logger.warning("Redis indisponível ao ler o estado da limpeza", exc_info=True)
        return {"disponivel": False, "motivo": "Redis indisponível."}
    finally:
        await redis.aclose()
    return {
        "disponivel": True,
        "relatorio": _decodificar(relatorio),
        "resultado": _decodificar(resultado),
        "pendente": bool(pendentes),
        "em_andamento": bool(andamento),
    }


async def solicitar_limpeza(solicitante: str) -> dict:
    """Enfileira UM pedido de limpeza. Levanta LimpezaJaSolicitada se já há um."""
    redis = cliente_redis()
    try:
        if await redis.llen(CHAVE_FILA) >= MAXIMO_PEDIDOS_PENDENTES or await redis.exists(CHAVE_ANDAMENTO):
            raise LimpezaJaSolicitada
        pedido = {
            "id": uuid.uuid4().hex,
            "solicitante": solicitante[:120],
            "solicitado_em": datetime.now(UTC).isoformat(),
        }
        await redis.rpush(CHAVE_FILA, json.dumps(pedido))
        return pedido
    finally:
        await redis.aclose()

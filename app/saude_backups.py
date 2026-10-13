"""Saúde da rotina de backups (painel do superadmin + alerta de plataforma).

A rotina diária (docker/rotina-diaria-backup.sh) registra cada etapa em
``backups/historico-rotina.jsonl`` (uma linha JSON por evento) e deixa
marcadores ``.offsite-<dump>`` (envio externo confirmado) e ``.pc-<dump>``
(cópia no PC validada por hash). Só o worker enxerga ``backups/`` (montado
somente leitura); ele consolida tudo num instantâneo guardado no Redis, que a
API lê sem precisar de acesso aos dumps.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.queueing import cliente_redis
from app.settings import get_settings

logger = logging.getLogger("ze_registra.saude_backups")

REDIS_CHAVE = "saude:backups"
REDIS_TTL_SEGUNDOS = 3 * 3600
ARQUIVO_HISTORICO = "historico-rotina.jsonl"
DIAS_HISTORICO = 14
QUEDA_TAMANHO_ATENCAO = 0.30
GRAVIDADE = {"ok": 0, "desconhecido": 1, "atencao": 2, "critico": 3}


def _agora() -> datetime:
    return datetime.now(UTC)


def _parse_data(valor: object) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return data if data.tzinfo else data.replace(tzinfo=UTC)


def _ler_eventos(diretorio: Path) -> list[dict]:
    caminho = diretorio / ARQUIVO_HISTORICO
    eventos: list[dict] = []
    try:
        linhas = caminho.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for linha in linhas:
        try:
            evento = json.loads(linha)
        except ValueError:
            continue
        if not isinstance(evento, dict):
            continue
        data = _parse_data(evento.get("em"))
        if data is None or evento.get("etapa") not in {"dump", "offsite", "simulado"}:
            continue
        evento["_em"] = data
        eventos.append(evento)
    eventos.sort(key=lambda item: item["_em"])
    return eventos


def _mtime(caminho: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(caminho.stat().st_mtime, UTC)
    except OSError:
        return None


def _marcador_mais_recente(diretorio: Path, prefixo: str) -> datetime | None:
    datas = [d for d in (_mtime(p) for p in diretorio.glob(f"{prefixo}*")) if d is not None]
    return max(datas) if datas else None


def _etapa(
    chave: str,
    nome: str,
    status: str,
    detalhe: str,
    ultima_em: datetime | None = None,
    tamanho_bytes: int | None = None,
) -> dict:
    return {
        "chave": chave,
        "nome": nome,
        "status": status,
        "detalhe": detalhe,
        "ultima_em": ultima_em.isoformat() if ultima_em else None,
        "tamanho_bytes": tamanho_bytes,
    }


def _horas(delta: timedelta) -> float:
    return delta.total_seconds() / 3600


def _avaliar_dump(diretorio: Path, eventos: list[dict], agora: datetime, limite_horas: float) -> dict:
    nome = "Dump do banco"
    dumps = [(p, _mtime(p)) for p in diretorio.glob("inpi-*.dump")]
    dumps = [(p, m) for p, m in dumps if m is not None]
    do_dump = [e for e in eventos if e["etapa"] == "dump"]
    ok_dump = [e for e in do_dump if e.get("ok")]
    ultimo_arquivo = max(dumps, key=lambda item: item[1]) if dumps else None
    candidatas = [e["_em"] for e in ok_dump] + ([ultimo_arquivo[1]] if ultimo_arquivo else [])
    ultima_ok = max(candidatas, default=None)
    if ultimo_arquivo:
        tamanho = ultimo_arquivo[0].stat().st_size
    else:
        tamanho = ok_dump[-1].get("tamanho_bytes") if ok_dump else None

    if ultima_ok is None:
        return _etapa("dump", nome, "critico", "Nenhum dump encontrado.")
    if do_dump and not do_dump[-1].get("ok") and do_dump[-1]["_em"] > ultima_ok:
        return _etapa("dump", nome, "critico", "A última execução do backup falhou.", do_dump[-1]["_em"], tamanho)
    idade = _horas(agora - ultima_ok)
    if idade > limite_horas:
        detalhe = f"Último dump tem {idade:.0f} h (limite {limite_horas:.0f} h)."
        return _etapa("dump", nome, "critico", detalhe, ultima_ok, tamanho)
    tamanhos = [int(e["tamanho_bytes"]) for e in ok_dump if isinstance(e.get("tamanho_bytes"), int)]
    if len(tamanhos) >= 2 and tamanhos[-2] > 0 and tamanhos[-1] < tamanhos[-2] * (1 - QUEDA_TAMANHO_ATENCAO):
        detalhe = "O tamanho do dump caiu mais de 30% em relação ao anterior."
        return _etapa("dump", nome, "atencao", detalhe, ultima_ok, tamanho)
    return _etapa("dump", nome, "ok", f"Há {idade:.0f} h.", ultima_ok, tamanho)


def _avaliar_offsite(diretorio: Path, eventos: list[dict], agora: datetime, limite_horas: float) -> dict:
    nome = "Envio externo cifrado"
    do_envio = [e for e in eventos if e["etapa"] == "offsite"]
    ok_envio = [e for e in do_envio if e.get("ok")]
    marcador = _marcador_mais_recente(diretorio, ".offsite-")
    candidatas = [e["_em"] for e in ok_envio] + ([marcador] if marcador else [])
    ultima_ok = max(candidatas, default=None)
    tamanho = ok_envio[-1].get("tamanho_bytes") if ok_envio else None
    if ultima_ok is None:
        status = "critico" if do_envio else "atencao"
        return _etapa("offsite", nome, status, "Nenhum envio externo concluído até agora.")
    if do_envio and not do_envio[-1].get("ok") and do_envio[-1]["_em"] > ultima_ok:
        return _etapa("offsite", nome, "critico", "O último envio externo falhou.", do_envio[-1]["_em"], tamanho)
    idade = _horas(agora - ultima_ok)
    if idade > limite_horas:
        detalhe = f"Último envio tem {idade:.0f} h (limite {limite_horas:.0f} h)."
        return _etapa("offsite", nome, "critico", detalhe, ultima_ok, tamanho)
    return _etapa("offsite", nome, "ok", f"Há {idade:.0f} h.", ultima_ok, tamanho)


def _avaliar_simulado(eventos: list[dict], agora: datetime, limite_dias: float) -> dict:
    nome = "Simulado de restauração"
    do_simulado = [e for e in eventos if e["etapa"] == "simulado"]
    if not do_simulado:
        return _etapa("simulado", nome, "atencao", "Nenhum simulado registrado ainda (roda aos domingos).")
    ultimo = do_simulado[-1]
    if not ultimo.get("ok"):
        return _etapa("simulado", nome, "critico", "O último simulado de restauração falhou.", ultimo["_em"])
    dias = _horas(agora - ultimo["_em"]) / 24
    if dias > limite_dias:
        detalhe = f"Último simulado bem-sucedido há {dias:.0f} dias (limite {limite_dias:.0f})."
        return _etapa("simulado", nome, "critico", detalhe, ultimo["_em"])
    return _etapa("simulado", nome, "ok", f"Há {dias:.0f} dia(s).", ultimo["_em"])


def _avaliar_copia_pc(diretorio: Path, agora: datetime, limite_horas: float) -> dict:
    nome = "Cópia no PC"
    marcador = _marcador_mais_recente(diretorio, ".pc-")
    if marcador is None:
        return _etapa("copia_pc", nome, "desconhecido", "Sem confirmação da cópia no PC (marcador .pc-* ausente).")
    idade = _horas(agora - marcador)
    if idade > limite_horas:
        detalhe = f"Última cópia validada há {idade:.0f} h (limite {limite_horas:.0f} h)."
        return _etapa("copia_pc", nome, "critico", detalhe, marcador)
    return _etapa("copia_pc", nome, "ok", f"Há {idade:.0f} h.", marcador)


def _avaliar_disco(caminho: str, atencao: float, critico: float) -> dict:
    nome = "Disco da VPS"
    try:
        uso = shutil.disk_usage(caminho)
    except OSError:
        return _etapa("disco", nome, "desconhecido", "Não foi possível ler o uso do disco.")
    percentual = uso.used / uso.total * 100 if uso.total else 0.0
    detalhe = f"{percentual:.0f}% usado, {uso.free / 1024**3:.0f} GB livres."
    status = "critico" if percentual >= critico else "atencao" if percentual >= atencao else "ok"
    return _etapa("disco", nome, status, detalhe)


def _historico(eventos: list[dict], agora: datetime) -> list[dict]:
    desde = (agora - timedelta(days=DIAS_HISTORICO)).date()
    dias: dict[str, dict] = {}
    for evento in eventos:
        data = evento["_em"].astimezone(UTC).date()
        if data < desde:
            continue
        linha = dias.setdefault(
            data.isoformat(), {"data": data.isoformat(), "dump": None, "offsite": None, "simulado": None}
        )
        linha[evento["etapa"]] = bool(evento.get("ok"))
    return [dias[chave] for chave in sorted(dias, reverse=True)]


def coletar_saude_backups(diretorio: Path, *, agora: datetime | None = None, caminho_disco: str = "/") -> dict:
    """Lê ``diretorio`` (backups/) e devolve o estado de cada etapa + resumo geral."""
    settings = get_settings()
    agora = agora or _agora()
    eventos = _ler_eventos(diretorio)
    etapas = [
        _avaliar_dump(diretorio, eventos, agora, settings.alerta_backup_max_horas),
        _avaliar_offsite(diretorio, eventos, agora, settings.alerta_offsite_max_horas),
        _avaliar_simulado(eventos, agora, settings.alerta_simulado_max_dias),
        _avaliar_copia_pc(diretorio, agora, settings.alerta_copia_pc_max_horas),
        _avaliar_disco(
            caminho_disco, settings.alerta_disco_atencao_percentual, settings.alerta_disco_critico_percentual
        ),
    ]
    geral = max((e["status"] for e in etapas), key=lambda s: GRAVIDADE[s])
    return {
        "coletado_em": agora.isoformat(),
        "geral": geral,
        "etapas": etapas,
        "historico": _historico(eventos, agora),
    }


async def publicar_snapshot(dados: dict) -> None:
    redis = cliente_redis()
    try:
        await redis.set(REDIS_CHAVE, json.dumps(dados), ex=REDIS_TTL_SEGUNDOS)
    finally:
        await redis.aclose()


async def ler_snapshot() -> dict | None:
    """Instantâneo mais recente publicado pelo worker, ou None se ausente/indisponível."""
    redis = cliente_redis()
    try:
        bruto = await redis.get(REDIS_CHAVE)
    except Exception:
        logger.warning("Redis indisponível ao ler o snapshot de saúde dos backups", exc_info=True)
        return None
    finally:
        await redis.aclose()
    if not bruto:
        return None
    try:
        return json.loads(bruto)
    except ValueError:
        return None

"""Alertas ativos: transforma o que o monitoramento já mede em avisos com dedup.

Hoje a TV mostra tudo, mas ninguém é avisado se ninguém olhar. Este avaliador
roda no worker a cada minuto, aplica os MESMOS limiares da TV e grava o estado
de cada condição em ``alertas_estado``. Ele não envia nada: decide QUANDO um
aviso é devido e registra em ``aviso_seq``/``aviso_tipo``; o notificador da VPS
(passo seguinte) lê ``GET /v1/monitoramento/alertas`` e avisa.

Regras (plano aprovado em 18/11/2026):

- crítico (qualquer hora): sistema fora 2+ min (incidente aberto da sonda),
  backup do dia ausente (dump ou envio externo crítico), sessão do WhatsApp
  (Evolution) fora por 15+ min;
- atenção (só das 07h às 22h, Brasília): status amarelo/vermelho, erro de
  servidor recorrente (mesma assinatura 3x na hora), backup de cliente do
  EZRA fora do dia;
- aviso só na mudança de estado (ok -> falha), lembrete a cada 4 h enquanto
  persistir, e um aviso de recuperação depois de 2 min sem a condição;
- fonte que não pôde ser lida NÃO conta como "tudo bem": o estado anterior
  daquela fonte fica como está.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.disponibilidade import ALVOS, coletar_disponibilidade
from app.models import AlertaEstado, EventoOperacional
from app.queueing import cliente_redis
from app.saude_backups import ler_snapshot
from app.settings import get_settings

logger = logging.getLogger("ze_registra.alertas_ativos")

FUSO = ZoneInfo("America/Sao_Paulo")
CRITICO = "critico"
ATENCAO = "atencao"
HORA_INICIO_ATENCAO = 7
HORA_FIM_ATENCAO = 22
LEMBRETE = timedelta(hours=4)
RECUPERACAO_APOS = timedelta(minutes=2)
EVOLUTION_CARENCIA = timedelta(minutes=15)
ERRO_RECORRENTE_MINIMO = 3
ERROS_RECORRENTES_MAX = 10
RETENCAO_INATIVOS = timedelta(days=7)
REDIS_AVALIADOR = "alertas:avaliador_em"
TIMEOUT_EVOLUTION_SEGUNDOS = 4.0

NOMES_ALVO = {"zeregistra": "Zé Registra", "erp": "EZRA ERP"}


@dataclass(frozen=True)
class Condicao:
    fingerprint: str
    severidade: str
    titulo: str
    detalhe: str = ""
    carencia: timedelta = timedelta(0)


@dataclass(frozen=True)
class Estado:
    """Espelho de uma linha de alertas_estado (testável sem banco)."""

    fingerprint: str
    severidade: str
    titulo: str
    detalhe: str
    desde_quando: datetime
    em_falha: bool = True
    confirmado: bool = False
    ausente_desde: datetime | None = None
    ultimo_aviso: datetime | None = None
    aviso_seq: int = 0
    aviso_tipo: str | None = None


def em_horario_de_atencao(agora: datetime) -> bool:
    return HORA_INICIO_ATENCAO <= agora.astimezone(FUSO).hour < HORA_FIM_ATENCAO


def _pode_avisar(severidade: str, agora: datetime) -> bool:
    return severidade == CRITICO or em_horario_de_atencao(agora)


def transicionar(estado: Estado | None, cond: Condicao | None, agora: datetime) -> Estado | None:
    """Próximo estado de um fingerprint, dado o estado salvo e a condição de agora (None = ok)."""
    if cond is not None:
        if estado is None or not estado.em_falha:
            estado = Estado(
                fingerprint=cond.fingerprint,
                severidade=cond.severidade,
                titulo=cond.titulo,
                detalhe=cond.detalhe,
                desde_quando=agora,
                aviso_seq=estado.aviso_seq if estado else 0,
            )
        # Voltou durante a espera da recuperação: continua o mesmo alerta, sem aviso novo.
        estado = replace(estado, ausente_desde=None, titulo=cond.titulo, detalhe=cond.detalhe)
        if cond.severidade != estado.severidade:
            # Subiu para crítico: vale um aviso novo. Desceu: segue o alerta atual.
            if cond.severidade == CRITICO:
                estado = replace(estado, ultimo_aviso=None)
            estado = replace(estado, severidade=cond.severidade)
        if not estado.confirmado and agora - estado.desde_quando >= cond.carencia:
            estado = replace(estado, confirmado=True)
        if estado.confirmado and _pode_avisar(estado.severidade, agora):
            if estado.ultimo_aviso is None:
                estado = replace(estado, ultimo_aviso=agora, aviso_seq=estado.aviso_seq + 1, aviso_tipo="novo")
            elif agora - estado.ultimo_aviso >= LEMBRETE:
                estado = replace(estado, ultimo_aviso=agora, aviso_seq=estado.aviso_seq + 1, aviso_tipo="lembrete")
        return estado

    if estado is None or not estado.em_falha:
        return estado
    if estado.ausente_desde is None:
        return replace(estado, ausente_desde=agora)
    if agora - estado.ausente_desde < RECUPERACAO_APOS:
        return estado
    avisado = estado.ultimo_aviso is not None
    if avisado and not _pode_avisar(estado.severidade, agora):
        return estado  # recuperação de atenção espera o horário comercial
    if avisado:
        return replace(
            estado,
            em_falha=False,
            confirmado=False,
            ultimo_aviso=None,
            aviso_seq=estado.aviso_seq + 1,
            aviso_tipo="recuperado",
        )
    return replace(estado, em_falha=False, confirmado=False, ultimo_aviso=None)


# ---------------------------------------------------------------- coletores
# Cada coletor devolve a lista de condições com falha, ou None quando a fonte
# não pôde ser lida (então o estado anterior dela não muda).


def _texto(valor: object, limite: int) -> str:
    return str(valor or "")[:limite]


def condicoes_disponibilidade(disp: dict) -> list[Condicao]:
    """Incidente aberto (2+ minutos fora, regra da faixa Disponibilidade da TV) = crítico."""
    condicoes = []
    for alvo in ALVOS:
        resumo = (disp.get("alvos") or {}).get(alvo) or {}
        if resumo.get("incidente_aberto"):
            nome = NOMES_ALVO[alvo]
            condicoes.append(
                Condicao(f"disp:{alvo}", CRITICO, f"{nome} fora do ar", "Sonda sem resposta há 2 minutos ou mais.")
            )
    return condicoes


def condicoes_backup_vps(snapshot: dict) -> list[Condicao]:
    """Dump do dia ausente/falho ou envio externo crítico (mesmos status da TV)."""
    condicoes = []
    rotulos = {"dump": "Backup do banco", "offsite": "Cópia externa do backup"}
    for etapa in snapshot.get("etapas") or []:
        if isinstance(etapa, dict) and etapa.get("chave") in rotulos and etapa.get("status") == "critico":
            condicoes.append(
                Condicao(
                    f"backup:{etapa['chave']}",
                    CRITICO,
                    f"{rotulos[etapa['chave']]} com problema",
                    _texto(etapa.get("detalhe"), 300),
                )
            )
    return condicoes


def condicoes_erp(erp: dict) -> list[Condicao]:
    """Status amarelo/vermelho do ERP e backup de cliente do EZRA fora do dia (atenção)."""
    condicoes = []
    saude = erp.get("saude")
    if saude in ("amarelo", "vermelho"):
        condicoes.append(Condicao("erp:status", ATENCAO, f"EZRA ERP em {saude}", _texto(erp.get("motivo"), 300)))
    backup = erp.get("backup") or {}
    if backup.get("disponivel") and backup.get("saude") in ("amarelo", "vermelho"):
        detalhe = (
            f"{backup.get('em_dia', 0)} de {backup.get('clientes_ativos', 0)} em dia; "
            f"{backup.get('atrasados', 0)} atrasado(s), {backup.get('sem_backup', 0)} sem backup, "
            f"{backup.get('com_erro', 0)} com erro."
        )
        condicoes.append(Condicao("erp:backup", ATENCAO, "Backup de cliente do EZRA fora do dia", detalhe))
    return condicoes


def condicoes_status_central(erros_5xx_1h: int) -> list[Condicao]:
    """Mesma regra da TV: 1 a 9 erros 5xx na hora = amarelo; 10+ = vermelho."""
    if erros_5xx_1h >= 10:
        return [
            Condicao(
                "zr:status", ATENCAO, "Zé Registra em vermelho", f"{erros_5xx_1h} erros de servidor na última hora."
            )
        ]
    if erros_5xx_1h >= 1:
        return [
            Condicao(
                "zr:status", ATENCAO, "Zé Registra em amarelo", f"{erros_5xx_1h} erro(s) de servidor na última hora."
            )
        ]
    return []


def condicoes_erros_recorrentes(grupos: list[tuple[str, int, int]]) -> list[Condicao]:
    """(operação, status, ocorrências) com 5xx: mesma assinatura 3+ vezes na hora."""
    condicoes = []
    for operacao, status, total in grupos:
        if total >= ERRO_RECORRENTE_MINIMO:
            condicoes.append(
                Condicao(
                    f"erros:{operacao[:120]}|{status}",
                    ATENCAO,
                    "Erro de servidor recorrente",
                    f"{_texto(operacao, 100)} respondeu {status} {total} vezes na última hora.",
                )
            )
    return condicoes


def condicao_evolution(resposta_estado: object | None, erro: str | None) -> list[Condicao]:
    """Sessão do WhatsApp (Evolution): só "open" é conectado. Alerta crítico após 15 min seguidos."""
    if erro is None and isinstance(resposta_estado, dict):
        instancia = resposta_estado.get("instance")
        estado = instancia.get("state") if isinstance(instancia, dict) else resposta_estado.get("state")
        if estado == "open":
            return []
        detalhe = f"Estado da sessão: {_texto(estado, 30) or 'desconhecido'}."
    else:
        detalhe = f"API do gateway sem resposta ({_texto(erro, 60) or 'resposta inválida'})."
    return [Condicao("evolution:sessao", CRITICO, "WhatsApp (Evolution) desconectado", detalhe, EVOLUTION_CARENCIA)]


async def _consultar_evolution() -> tuple[object | None, str | None]:
    config = get_settings()
    url = f"{config.evolution_api_url.rstrip('/')}/instance/connectionState/{config.evolution_instance}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_EVOLUTION_SEGUNDOS) as cliente:
            resposta = await cliente.get(url, headers={"apikey": config.evolution_api_key})
        resposta.raise_for_status()
        return resposta.json(), None
    except Exception as erro:  # noqa: BLE001 -- qualquer falha vira "sem resposta" (alerta só após a carência)
        return None, type(erro).__name__


async def _erros_5xx_na_hora(session: AsyncSession, agora: datetime) -> tuple[int, list[tuple[str, int, int]]]:
    desde = agora - timedelta(hours=1)
    total = (
        await session.execute(
            select(func.count()).where(EventoOperacional.criado_em >= desde, EventoOperacional.status_http >= 500)
        )
    ).scalar_one()
    quantidade = func.count()
    grupos = (
        await session.execute(
            select(EventoOperacional.operacao, EventoOperacional.status_http, quantidade)
            .where(EventoOperacional.criado_em >= desde, EventoOperacional.status_http >= 500)
            .group_by(EventoOperacional.operacao, EventoOperacional.status_http)
            .having(quantidade >= ERRO_RECORRENTE_MINIMO)
            .order_by(quantidade.desc())
            .limit(ERROS_RECORRENTES_MAX)
        )
    ).all()
    return int(total or 0), [(str(op), int(st), int(n)) for op, st, n in grupos]


async def coletar_condicoes(session: AsyncSession, agora: datetime) -> tuple[list[Condicao], set[str]]:
    """Condições com falha agora e os prefixos de fonte que foram realmente avaliados."""
    config = get_settings()
    condicoes: list[Condicao] = []
    avaliadas: set[str] = set()

    # Disponibilidade: lida direto da sonda (o instantâneo da TV só atualiza de hora em hora).
    diretorio = Path(config.backups_dir)
    if config.app_env.lower() == "production" and diretorio.is_dir():
        try:
            disp = await asyncio.to_thread(coletar_disponibilidade, diretorio, agora=agora)
            condicoes += condicoes_disponibilidade(disp)
            avaliadas.add("disp:")
        except Exception:  # noqa: BLE001
            logger.exception("Alertas: falha ao ler a disponibilidade")

    try:
        snapshot = await asyncio.wait_for(ler_snapshot(), timeout=2.0)
    except Exception:  # noqa: BLE001
        snapshot = None
    if isinstance(snapshot, dict):
        condicoes += condicoes_backup_vps(snapshot)
        avaliadas.add("backup:")

    try:
        total_5xx, grupos = await _erros_5xx_na_hora(session, agora)
        condicoes += condicoes_status_central(total_5xx)
        condicoes += condicoes_erros_recorrentes(grupos)
        avaliadas |= {"zr:", "erros:"}
    except Exception:  # noqa: BLE001
        logger.exception("Alertas: falha ao ler os erros da última hora")

    try:
        from app.api.tv_monitoramento import resumo_erp  # noqa: PLC0415 -- evita ciclo de import na carga do worker

        erp = await resumo_erp()
        if erp.get("status") == "ok" and isinstance(erp.get("dados"), dict):
            condicoes += condicoes_erp(erp["dados"])
            avaliadas.add("erp:")
    except Exception:  # noqa: BLE001
        logger.exception("Alertas: falha ao ler o resumo do ERP")

    if config.evolution_api_url and config.evolution_api_key and config.evolution_instance:
        resposta, erro = await _consultar_evolution()
        condicoes += condicao_evolution(resposta, erro)
        avaliadas.add("evolution:")
    return condicoes, avaliadas


# ---------------------------------------------------------------- persistência


def _de_linha(linha: AlertaEstado) -> Estado:
    return Estado(
        fingerprint=linha.fingerprint,
        severidade=linha.severidade,
        titulo=linha.titulo,
        detalhe=linha.detalhe or "",
        desde_quando=linha.desde_quando,
        em_falha=linha.em_falha,
        confirmado=linha.confirmado,
        ausente_desde=linha.ausente_desde,
        ultimo_aviso=linha.ultimo_aviso,
        aviso_seq=linha.aviso_seq,
        aviso_tipo=linha.aviso_tipo,
    )


_CAMPOS = (
    "severidade",
    "titulo",
    "detalhe",
    "desde_quando",
    "em_falha",
    "confirmado",
    "ausente_desde",
    "ultimo_aviso",
    "aviso_seq",
    "aviso_tipo",
)


async def aplicar_condicoes(
    session: AsyncSession, condicoes: list[Condicao], avaliadas: set[str], agora: datetime
) -> None:
    """Aplica as transições e grava. Só mexe em fingerprints das fontes avaliadas."""
    por_fp = {c.fingerprint: c for c in condicoes}
    linhas = {linha.fingerprint: linha for linha in (await session.execute(select(AlertaEstado))).scalars()}
    for fp in set(por_fp) | set(linhas):
        if not any(fp.startswith(prefixo) for prefixo in avaliadas):
            continue
        linha = linhas.get(fp)
        anterior = _de_linha(linha) if linha else None
        novo = transicionar(anterior, por_fp.get(fp), agora)
        if novo is None or novo == anterior:
            continue
        if linha is None:
            session.add(AlertaEstado(fingerprint=fp, **{campo: getattr(novo, campo) for campo in _CAMPOS}))
        else:
            for campo in _CAMPOS:
                setattr(linha, campo, getattr(novo, campo))
            linha.atualizado_em = agora
    await session.execute(
        delete(AlertaEstado).where(
            AlertaEstado.em_falha.is_(False), AlertaEstado.atualizado_em < agora - RETENCAO_INATIVOS
        )
    )


async def _marcar_avaliacao(agora: datetime) -> None:
    redis = cliente_redis()
    try:
        await redis.set(REDIS_AVALIADOR, agora.isoformat(), ex=3600)
    finally:
        await redis.aclose()


async def avaliar_alertas(session: AsyncSession, *, agora: datetime | None = None) -> None:
    """Tarefa do worker (a cada minuto): coleta, aplica transições e marca o batimento do avaliador."""
    if not get_settings().alertas_ativos_enabled:
        return
    agora = agora or datetime.now(UTC)
    condicoes, avaliadas = await coletar_condicoes(session, agora)
    await aplicar_condicoes(session, condicoes, avaliadas, agora)
    await session.flush()
    await _marcar_avaliacao(agora)


async def ler_avaliador_em() -> str | None:
    redis = cliente_redis()
    try:
        return await redis.get(REDIS_AVALIADOR)
    except Exception:  # noqa: BLE001
        return None
    finally:
        await redis.aclose()

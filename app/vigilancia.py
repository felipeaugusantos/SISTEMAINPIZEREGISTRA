"""Regras puras e execução idempotente da vigilância preventiva."""
from __future__ import annotations

from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    ClientePortal,
    ColidenciaVigilancia,
    HistoricoAlertaVigilancia,
    Lead,
    PreferenciaVigilancia,
    Processo,
    VigilanciaExecucao,
)


def calcular_score_risco(marca: str, titulo: str, nice: set[str] | None = None,
                         viena: set[str] | None = None,
                         nice_config: set[str] | None = None,
                         viena_config: set[str] | None = None) -> tuple[float, dict[str, Any]]:
    """Calcula score determinístico (0–100) e fatores explicáveis."""
    marca_n = " ".join((marca or "").lower().split())
    titulo_n = " ".join((titulo or "").lower().split())
    nome = round(SequenceMatcher(None, marca_n, titulo_n).ratio() * 100, 2) if marca_n and titulo_n else 0.0
    nice_overlap = sorted((nice or set()).intersection(nice_config or set()))
    viena_overlap = sorted((viena or set()).intersection(viena_config or set()))
    fatores = {
        "nome": nome,
        "classes_nice": nice_overlap,
        "codigos_viena": viena_overlap,
        "regra": "vigilancia_preventiva",
    }
    score = min(100.0, round(nome * 0.70 + (20.0 if nice_overlap else 0.0) + (10.0 if viena_overlap else 0.0), 2))
    return score, fatores


def validar_comunicacao(*, preferencia: PreferenciaVigilancia | None,
                        colidencia: ColidenciaVigilancia, canal: str) -> tuple[bool, str]:
    """Gate único: regra, evidência, justificativa, aprovação e canal ativo."""
    if preferencia is None or not preferencia.ativo:
        return False, "preferencia de vigilancia inativa ou inexistente"
    if canal not in (preferencia.canais or []):
        return False, "canal nao habilitado nas preferencias"
    if not colidencia.evidencias or not colidencia.evidencias.get("regra"):
        return False, "regra e evidencias obrigatorias"
    if not (colidencia.justificativa or "").strip():
        return False, "justificativa obrigatoria"
    if colidencia.status != "aprovado" or not colidencia.aprovado_por or not colidencia.aprovado_em:
        return False, "aprovacao humana obrigatoria"
    if colidencia.falso_positivo:
        return False, "colidencia marcada como falso positivo"
    return True, "ok"


async def enfileirar_alerta(session: AsyncSession, colidencia: ColidenciaVigilancia,
                            preferencia: PreferenciaVigilancia, canal: str) -> HistoricoAlertaVigilancia:
    permitido, motivo = validar_comunicacao(preferencia=preferencia, colidencia=colidencia, canal=canal)
    if not permitido:
        raise ValueError(motivo)
    chave = f"vigilancia:{colidencia.id}:{canal}:v1"
    existente = (await session.execute(select(HistoricoAlertaVigilancia).where(
        HistoricoAlertaVigilancia.idempotency_key == chave
    ))).scalar_one_or_none()
    if existente:
        return existente
    alerta = HistoricoAlertaVigilancia(
        organizacao_id=colidencia.organizacao_id, colidencia_id=colidencia.id,
        cliente_id=colidencia.cliente_id, canal=canal, status="pendente",
        idempotency_key=chave, justificativa=colidencia.justificativa,
        detalhes={"evidencias": colidencia.evidencias, "score_risco": colidencia.score_risco},
    )
    session.add(alerta)
    return alerta


async def executar_vigilancia_semanal(session: AsyncSession, organizacao_id: int | None = None) -> dict:
    """Executa a semana corrente. A chave única torna reexecuções idempotentes."""
    semana = datetime.now(UTC).date().isocalendar()
    chave = f"{semana.year}-W{semana.week:02d}"
    org_ids = [organizacao_id] if organizacao_id else list((await session.execute(select(PreferenciaVigilancia.organizacao_id).where(PreferenciaVigilancia.ativo.is_(True)).distinct())).scalars())
    total = 0
    criadas = 0
    for org_id in org_ids:
        execucao = (await session.execute(select(VigilanciaExecucao).where(
            VigilanciaExecucao.organizacao_id == org_id, VigilanciaExecucao.chave == chave
        ))).scalar_one_or_none()
        if execucao and execucao.status == "concluida":
            continue
        if execucao is None:
            execucao = VigilanciaExecucao(organizacao_id=org_id, chave=chave, frequencia="semanal")
            session.add(execucao)
            await session.flush()
        preferencias = (await session.execute(select(PreferenciaVigilancia).where(
            PreferenciaVigilancia.organizacao_id == org_id, PreferenciaVigilancia.ativo.is_(True)
        ))).scalars().all()
        for pref in preferencias:
            cliente = await session.get(ClientePortal, pref.cliente_id)
            if not cliente:
                continue
            lead = await session.get(Lead, cliente.lead_id)
            if not lead or not (lead.marca or '').strip():
                continue
            processos = (await session.execute(select(Processo).options(selectinload(Processo.classificacoes)).where(
                Processo.fonte.ilike('%rpi%')
            ).limit(500))).scalars().all()
            classes_cfg, viena_cfg = set(pref.classes_nice or []), set(pref.codigos_viena or [])
            for processo in processos:
                nice = {c.codigo for c in processo.classificacoes if c.sistema.lower() in ('nice', 'ncl')}
                viena = {c.codigo for c in processo.classificacoes if c.sistema.lower() in ('vienna', 'viena')}
                if classes_cfg or viena_cfg:
                    if not (nice.intersection(classes_cfg) or viena.intersection(viena_cfg)):
                        continue
                score, fatores = calcular_score_risco(lead.marca, processo.titulo or processo.elemento_nominativo or '', nice, viena, classes_cfg, viena_cfg)
                total += 1
                existente = (await session.execute(select(ColidenciaVigilancia).where(
                    ColidenciaVigilancia.organizacao_id == org_id, ColidenciaVigilancia.cliente_id == cliente.id,
                    ColidenciaVigilancia.processo_id == processo.id
                ))).scalar_one_or_none()
                if existente:
                    continue
                session.add(ColidenciaVigilancia(
                    organizacao_id=org_id, cliente_id=cliente.id, processo_id=processo.id,
                    rpi_numero=next((m.numero_rpi for m in processo.movimentacoes if m.numero_rpi), None),
                    score_risco=score, evidencias={**fatores, "marca_monitorada": lead.marca, "processo": processo.numero, "titulo": processo.titulo},
                    justificativa="Coincidencia identificada por nome, Nice e/ou Viena; requer revisao humana.",
                ))
                criadas += 1
        execucao.encontrados, execucao.criados, execucao.status, execucao.finalizado_em = total, criadas, "concluida", datetime.now(UTC)
    await session.commit()
    return {"chave": chave, "encontrados": total, "criadas": criadas, "status": "concluida"}

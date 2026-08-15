from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import engine, get_session
from app.models import EventoOperacional, RpiImportacao, RpiSyncEstado, RpiSyncExecucao
from app.queueing import status_fila
from app.rpi.health import avaliar_saude_rpi
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin", tags=["observabilidade"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
TechDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("production.view"))]


def _exigir_acesso_tech(usuario: UsuarioAutenticado) -> UsuarioAutenticado:
    if not (usuario.superadmin or usuario.perfil in {"administrador", "tech"}):
        raise HTTPException(status_code=403, detail="Acesso restrito ao departamento de Tech")
    return usuario


@router.get("/observabilidade")
async def obter_observabilidade(session: SessionDep, usuario: TechDep) -> dict:
    _exigir_acesso_tech(usuario)
    agora = datetime.now(UTC)
    desde = agora - timedelta(hours=24)
    settings = get_settings()

    banco = {"status": "ok", "latencia_ms": 0.0, "conexoes": None, "tamanho_bytes": None}
    inicio = datetime.now(UTC)
    try:
        await session.execute(text("SELECT 1"))
        banco["latencia_ms"] = round((datetime.now(UTC) - inicio).total_seconds() * 1000, 2)
        banco["conexoes"] = int(
            await session.scalar(
                text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
            )
            or 0
        )
        banco["tamanho_bytes"] = int(
            await session.scalar(text("SELECT pg_database_size(current_database())")) or 0
        )
    except Exception as exc:
        banco = {"status": "indisponivel", "erro": type(exc).__name__}

    fila = await status_fila()
    estado = await session.get(RpiSyncEstado, 1)
    ultima = (
        await session.execute(
            select(RpiImportacao)
            .where(RpiImportacao.tipo == "marca")
            .order_by(RpiImportacao.numero_rpi.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    ultima_execucao = (
        await session.execute(
            select(RpiSyncExecucao)
            .where(RpiSyncExecucao.finalizado_em.is_not(None))
            .order_by(RpiSyncExecucao.finalizado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    quantidade_erros = int(
        await session.scalar(
            select(func.count()).select_from(RpiSyncExecucao).where(RpiSyncExecucao.status == "falhou")
        )
        or 0
    )
    ultima_sincronizacao = (
        estado.ultima_verificacao_em if estado and estado.ultima_verificacao_em else None
    ) or (ultima.importado_em if ultima else None)
    status_rpi, idade_horas, motivos = avaliar_saude_rpi(
        status_sync=estado.status if estado else None,
        ultima_rpi_oficial=estado.ultima_rpi_oficial if estado else None,
        ultima_rpi_importada=ultima.numero_rpi if ultima else None,
        ultima_sincronizacao=ultima_sincronizacao,
        status_integridade=ultima.status_integridade if ultima else None,
        limite_atraso_horas=settings.rpi_stale_hours,
    )
    metricas = (
        await session.execute(
            select(
                func.count(),
                func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0)),
                func.avg(EventoOperacional.duracao_ms),
            ).where(EventoOperacional.criado_em >= desde)
        )
    ).one()
    requisicoes = int(metricas[0] or 0)
    erros = int(metricas[1] or 0)
    pool = engine.pool
    return {
        "gerado_em": agora,
        "ambiente": settings.app_env,
        "servicos": {
            "api": {"status": "ok", "versao": "0.1.0"},
            "site": {"status": "ok", "url": settings.app_public_url},
            "banco": banco,
            "redis": fila,
            "worker": {"status": "ok" if fila["status"] == "ok" else "atencao"},
            "rpi_sync": {"status": status_rpi, "ultima_execucao": ultima_execucao.status if ultima_execucao else None},
        },
        "rpi": {
            "status": status_rpi,
            "idade_horas": idade_horas,
            "ultima_rpi": ultima.numero_rpi if ultima else None,
            "status_integridade": ultima.status_integridade if ultima else "desconhecido",
            "anomalias": ultima.anomalias if ultima else [],
            "erros_acumulados": quantidade_erros,
            "motivos": motivos,
        },
        "api_24h": {
            "requisicoes": requisicoes,
            "erros": erros,
            "taxa_erros": erros / requisicoes if requisicoes else 0,
            "duracao_media_ms": float(metricas[2] or 0),
        },
        "pool": {
            "tamanho": pool.size() if hasattr(pool, "size") else None,
            "em_uso": pool.checkedout() if hasattr(pool, "checkedout") else None,
            "overflow": pool.overflow() if hasattr(pool, "overflow") else None,
        },
    }

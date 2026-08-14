from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AcaoAdminDep, UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import RpiImportacao, RpiSyncEstado, RpiSyncExecucao
from app.request_context import request_id_atual
from app.schemas import (
    RpiSyncAcaoResponse,
    RpiSyncAdminResponse,
    RpiSyncExecucaoResponse,
    RpiSyncSaudeResponse,
)
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin/rpi", tags=["atualização RPI"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("rpi.view"))]
SyncDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("rpi.sync"))]
STATUS_ATIVOS = ("solicitada", "verificando", "importando")


def pode_ver_execucoes_recentes(usuario: UsuarioAutenticado) -> bool:
    return usuario.perfil in {"tech", "administrador"}


def _execucao_response(item: RpiSyncExecucao) -> RpiSyncExecucaoResponse:
    progresso = (
        min(100.0, item.edicoes_processadas * 100 / item.edicoes_total)
        if item.edicoes_total
        else (100.0 if item.status in {"concluida", "sem_atualizacoes"} else 0.0)
    )
    fim = item.finalizado_em or (datetime.now(UTC) if item.iniciado_em else None)
    duracao = (
        max(0.0, (fim - item.iniciado_em).total_seconds())
        if fim is not None and item.iniciado_em is not None
        else None
    )
    return RpiSyncExecucaoResponse(
        id=item.id,
        origem=item.origem,
        status=item.status,
        solicitado_por=item.solicitado_por,
        request_id=item.request_id,
        execucao_anterior_id=item.execucao_anterior_id,
        rpi_inicio=item.rpi_inicio,
        rpi_fim=item.rpi_fim,
        rpi_atual=item.rpi_atual,
        ultima_rpi_oficial=item.ultima_rpi_oficial,
        ultima_rpi_local_antes=item.ultima_rpi_local_antes,
        ultima_rpi_local_depois=item.ultima_rpi_local_depois,
        edicoes_total=item.edicoes_total,
        edicoes_processadas=item.edicoes_processadas,
        progresso_percentual=round(progresso, 1),
        registros_processados=item.registros_processados,
        titulares_processados=item.titulares_processados,
        classes_processadas=item.classes_processadas,
        movimentacoes_processadas=item.movimentacoes_processadas,
        mensagem=item.mensagem,
        erro=item.erro,
        solicitado_em=item.solicitado_em,
        iniciado_em=item.iniciado_em,
        heartbeat_em=item.heartbeat_em,
        finalizado_em=item.finalizado_em,
        duracao_segundos=round(duracao, 1) if duracao is not None else None,
    )


def _aparencia_status(
    status_atual: str,
    edicoes_atraso: int,
    sincronizador_online: bool,
) -> tuple[str, str]:
    if not sincronizador_online:
        return "Sincronizador indisponível", "cinza"
    if status_atual == "falhou" or edicoes_atraso > 1:
        return "Falha ou atraso crítico", "vermelho"
    if status_atual in STATUS_ATIVOS:
        return "Atualização em andamento", "amarelo"
    if edicoes_atraso == 1:
        return "Nova RPI aguardando importação", "amarelo"
    return "Base atualizada", "verde"


async def _execucao_ativa(session: AsyncSession) -> RpiSyncExecucao | None:
    return (
        await session.execute(
            select(RpiSyncExecucao)
            .where(RpiSyncExecucao.status.in_(STATUS_ATIVOS))
            .order_by(RpiSyncExecucao.solicitado_em)
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("", response_model=RpiSyncAdminResponse)
async def obter_monitoramento_rpi(
    session: SessionDep,
    usuario: AdminDep,
) -> RpiSyncAdminResponse:
    settings = get_settings()
    estado = await session.get(RpiSyncEstado, 1)
    ultima_local = await session.scalar(
        select(func.max(RpiImportacao.numero_rpi)).where(RpiImportacao.tipo == "marca")
    )
    historico = (
        (
            await session.execute(
                select(RpiSyncExecucao).order_by(RpiSyncExecucao.solicitado_em.desc()).limit(30)
            )
        )
        .scalars()
        .all()
    )
    heartbeat = estado.heartbeat_em if estado else None
    limite_heartbeat = datetime.now(UTC) - timedelta(
        seconds=max(60, settings.rpi_sync_poll_seconds * 3)
    )
    sincronizador_online = bool(heartbeat and heartbeat >= limite_heartbeat)
    ultima_oficial = estado.ultima_rpi_oficial if estado else None
    edicoes_atraso = (
        max(0, ultima_oficial - ultima_local)
        if ultima_oficial is not None and ultima_local is not None
        else 0
    )
    status_atual = estado.status if estado else "nao_iniciado"
    rotulo, cor = _aparencia_status(status_atual, edicoes_atraso, sincronizador_online)
    atual = next((item for item in historico if item.status in STATUS_ATIVOS), None)
    return RpiSyncAdminResponse(
        status=status_atual,
        status_rotulo=rotulo,
        status_cor=cor,
        ultima_rpi_oficial=ultima_oficial,
        ultima_rpi_local=ultima_local,
        edicoes_atraso=edicoes_atraso,
        ultima_verificacao_em=estado.ultima_verificacao_em if estado else None,
        proxima_verificacao_em=estado.proxima_verificacao_em if estado else None,
        heartbeat_em=heartbeat,
        falhas_consecutivas=estado.falhas_consecutivas if estado else 0,
        ultimo_erro=estado.ultimo_erro if estado else None,
        intervalo_segundos=settings.rpi_sync_interval_seconds,
        saude=RpiSyncSaudeResponse(api=True, banco=True, sincronizador=sincronizador_online),
        execucao_atual=_execucao_response(atual) if atual else None,
        historico=(
            [_execucao_response(item) for item in historico]
            if pode_ver_execucoes_recentes(usuario)
            else []
        ),
    )


@router.post("/sincronizar", response_model=RpiSyncAcaoResponse, status_code=202)
async def solicitar_sincronizacao(
    session: SessionDep,
    administrador: SyncDep,
    _limite: AcaoAdminDep = None,
) -> RpiSyncAcaoResponse:
    ativa = await _execucao_ativa(session)
    if ativa is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Já existe uma sincronização ativa (execução {ativa.id})",
        )
    execucao = RpiSyncExecucao(
        origem="manual",
        status="solicitada",
        solicitado_por=administrador.email,
        request_id=request_id_atual(),
    )
    session.add(execucao)
    await session.commit()
    await session.refresh(execucao)
    return RpiSyncAcaoResponse(
        id=execucao.id,
        status=execucao.status,
        mensagem="Verificação adicionada à fila",
    )


@router.post(
    "/sincronizacoes/{execucao_id}/tentar-novamente",
    response_model=RpiSyncAcaoResponse,
    status_code=202,
)
async def tentar_novamente(
    execucao_id: int,
    session: SessionDep,
    administrador: SyncDep,
    _limite: AcaoAdminDep = None,
) -> RpiSyncAcaoResponse:
    anterior = await session.get(RpiSyncExecucao, execucao_id)
    if anterior is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada")
    if anterior.status != "falhou":
        raise HTTPException(
            status_code=409,
            detail="Somente execuções com falha podem ser repetidas",
        )
    ativa = await _execucao_ativa(session)
    if ativa is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe uma sincronização ativa (execução {ativa.id})",
        )
    execucao = RpiSyncExecucao(
        origem="reprocessamento",
        status="solicitada",
        solicitado_por=administrador.email,
        execucao_anterior_id=anterior.id,
        request_id=request_id_atual(),
    )
    session.add(execucao)
    await session.commit()
    await session.refresh(execucao)
    return RpiSyncAcaoResponse(
        id=execucao.id,
        status=execucao.status,
        mensagem="Nova tentativa adicionada à fila",
    )

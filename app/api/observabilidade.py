from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import engine, get_session
from app.models import EventoAuditoria, EventoOperacional, Organizacao, RpiImportacao, RpiSyncEstado, RpiSyncExecucao
from app.proxy import cliente_ip
from app.queueing import descartar_falha, listar_falhas, reprocessar_falha, status_fila
from app.rpi.health import avaliar_saude_rpi
from app.security_ext import proteger_segredo
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin", tags=["observabilidade"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
TechDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("production.view"))]


class ClicksignConfigInput(BaseModel):
    habilitado: bool = False
    ambiente: str = Field(default="sandbox", pattern="^(sandbox|producao)$")
    webhook_url: str = Field(default="", max_length=500)
    api_token: str | None = Field(default=None, max_length=500)
    webhook_secret: str | None = Field(default=None, max_length=500)


@router.get("/configuracao/clicksign")
async def configuracao_clicksign(session: SessionDep, usuario: TechDep) -> dict:
    """Exibe somente o estado seguro da integração; o token nunca é retornado."""
    _exigir_acesso_tech(usuario)
    settings = get_settings()
    org = await session.get(Organizacao, usuario.organizacao_id)
    saved = ((org.branding or {}).get("clicksign") if org else None) or {}
    base_url = saved.get("base_url") or settings.clicksign_base_url
    webhook_url = saved.get("webhook_url") or settings.clicksign_webhook_url
    token_configurado = bool(saved.get("api_token_enc") or settings.clicksign_api_token)
    secret_configurado = bool(saved.get("webhook_secret_enc") or settings.clicksign_webhook_secret)
    return {
        "habilitado": saved.get("habilitado", settings.clicksign_enabled),
        "ambiente": "sandbox" if "sandbox" in base_url else "producao",
        "base_url": base_url,
        "webhook_url": webhook_url or None,
        "token_configurado": token_configurado,
        "webhook_segredo_configurado": secret_configurado,
    }


@router.put("/configuracao/clicksign")
async def salvar_configuracao_clicksign(dados: ClicksignConfigInput, session: SessionDep, usuario: TechDep) -> dict:
    _exigir_acesso_tech(usuario)
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organização não encontrada")
    branding = dict(org.branding or {})
    anterior = dict(branding.get("clicksign") or {})
    base_url = (
        "https://sandbox.clicksign.com/api/v3" if dados.ambiente == "sandbox" else "https://app.clicksign.com/api/v3"
    )
    config = {
        "habilitado": dados.habilitado,
        "base_url": base_url,
        "webhook_url": dados.webhook_url.strip(),
    }
    config["api_token_enc"] = (
        proteger_segredo(dados.api_token.strip())
        if dados.api_token and dados.api_token.strip()
        else anterior.get("api_token_enc")
    )
    config["webhook_secret_enc"] = (
        proteger_segredo(dados.webhook_secret.strip())
        if dados.webhook_secret and dados.webhook_secret.strip()
        else anterior.get("webhook_secret_enc")
    )
    branding["clicksign"] = config
    org.branding = branding
    await session.commit()
    return {"ok": True, "mensagem": "Configuração Clicksign salva com segurança."}


def _exigir_acesso_tech(usuario: UsuarioAutenticado) -> UsuarioAutenticado:
    if not (usuario.superadmin or usuario.perfil in {"administrador", "tech"}):
        raise HTTPException(status_code=403, detail="Acesso restrito ao departamento de Tech")
    return usuario


def _auditar_fila(session: AsyncSession, request: Request, usuario: UsuarioAutenticado, acao: str, job_id: str) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao=acao[:20],
            recurso=f"fila_falha:{job_id}"[:180],
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes={},
        )
    )


# --- Achado FASE6-4 da auditoria (04/09/2026): a dead-letter queue (jobs
# que esgotaram as tentativas, FAILED_KEY em app/queueing.py) só era
# exposta como uma contagem (status_fila) -- ninguém conseguia ver o que
# tinha falhado nem reprocessar/descartar sem acessar redis-cli
# diretamente na VPS. ---


@router.get("/fila/falhas")
async def listar_fila_falhas(
    usuario: TechDep, limite: Annotated[int, Query(ge=1, le=200)] = 50, deslocamento: Annotated[int, Query(ge=0)] = 0
) -> dict:
    _exigir_acesso_tech(usuario)
    itens = await listar_falhas(limite=limite, deslocamento=deslocamento)
    for item in itens:
        item.pop("_bruto", None)
    return {"itens": itens, "limite": limite, "deslocamento": deslocamento}


@router.post("/fila/falhas/{job_id}/reprocessar")
async def reprocessar_fila_falha(
    job_id: str, request: Request, session: SessionDep, usuario: TechDep
) -> dict:
    _exigir_acesso_tech(usuario)
    encontrado = await reprocessar_falha(job_id)
    if not encontrado:
        raise HTTPException(404, "Job não encontrado na fila de falhas (já reprocessado ou removido).")
    _auditar_fila(session, request, usuario, "reprocessar_falha", job_id)
    await session.commit()
    return {"reprocessado": True}


@router.delete("/fila/falhas/{job_id}")
async def descartar_fila_falha(job_id: str, request: Request, session: SessionDep, usuario: TechDep) -> dict:
    _exigir_acesso_tech(usuario)
    encontrado = await descartar_falha(job_id)
    if not encontrado:
        raise HTTPException(404, "Job não encontrado na fila de falhas (já reprocessado ou removido).")
    _auditar_fila(session, request, usuario, "descartar_falha", job_id)
    await session.commit()
    return {"descartado": True}


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
            await session.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")) or 0
        )
        banco["tamanho_bytes"] = int(await session.scalar(text("SELECT pg_database_size(current_database())")) or 0)
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
    ultima_sincronizacao = (estado.ultima_verificacao_em if estado and estado.ultima_verificacao_em else None) or (
        ultima.importado_em if ultima else None
    )
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
            "rpi_sync": {
                "status": status_rpi,
                "ultima_execucao": ultima_execucao.status if ultima_execucao else None,
            },
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

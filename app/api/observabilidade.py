import os
import shutil
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auditing import criar_evento_auditoria
from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import engine, get_session
from app.feature_flags import obter_flag
from app.models import (
    EventoAuditoria,
    EventoOperacional,
    FeatureFlag,
    FeatureFlagEvento,
    Organizacao,
    ProcessoHeartbeat,
    RpiImportacao,
    RpiSyncEstado,
    RpiSyncExecucao,
    VersaoSistema,
)
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


# --- Achado de uma auditoria sistemática (08/09/2026, mesmo padrão do
# achado de EnvioCadenciaEmail): EventoOperacional é gravado a cada
# requisição (app/observability.py, componente/operacao/codigo_erro/
# detalhes) desde o início, mas os únicos consumidores existentes
# (obter_observabilidade acima, painel executivo em app/main.py, painel
# de produção) só usam func.count()/func.avg() -- nenhum endpoint deixava
# listar as linhas individuais pra investigar QUAL erro aconteceu. ---


@router.get("/observabilidade/eventos")
async def listar_eventos_operacionais(
    session: SessionDep,
    usuario: TechDep,
    componente: Annotated[str | None, Query(max_length=40)] = None,
    apenas_erros: Annotated[bool, Query()] = False,
    codigo_erro: Annotated[str | None, Query(max_length=100)] = None,
    horas: Annotated[int, Query(ge=1, le=720)] = 24,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    _exigir_acesso_tech(usuario)
    desde = datetime.now(UTC) - timedelta(hours=horas)
    filtros = [EventoOperacional.criado_em >= desde]
    if componente:
        filtros.append(EventoOperacional.componente == componente)
    if apenas_erros:
        filtros.append(EventoOperacional.sucesso.is_(False))
    if codigo_erro:
        filtros.append(EventoOperacional.codigo_erro == codigo_erro)
    total = int((await session.execute(select(func.count()).select_from(EventoOperacional).where(*filtros))).scalar_one())
    itens = (
        (
            await session.execute(
                select(EventoOperacional)
                .where(*filtros)
                .order_by(EventoOperacional.criado_em.desc())
                .limit(limite)
                .offset(deslocamento)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total": total,
        "limite": limite,
        "deslocamento": deslocamento,
        "itens": [
            {
                "id": item.id,
                "componente": item.componente,
                "operacao": item.operacao,
                "request_id": item.request_id,
                "sucesso": item.sucesso,
                "duracao_ms": item.duracao_ms,
                "status_http": item.status_http,
                "codigo_erro": item.codigo_erro,
                "detalhes": item.detalhes,
                "criado_em": item.criado_em,
            }
            for item in itens
        ],
    }


# --- Fase 7: painel técnico de observabilidade e rollback. Critério de
# aceite: a equipe identifica rapidamente uma regressão e consegue limitar
# seu impacto. Junta o que já existia espalhado (versão/commit, saúde de
# banco/fila/RPI, feature flags, telemetria por grupo da Fase 5) com o que
# faltava (heartbeat do worker, migration atual, erros por versão,
# organizações afetadas, resumo do último deploy) numa tela só. ---

LIMIAR_HEARTBEAT_WORKER_SEGUNDOS = 90
QUANTIDADE_VERSOES_PAINEL = 5
QUANTIDADE_ENDPOINTS_LATENCIA_PAINEL = 10
MINIMO_REQUISICOES_LATENCIA_ENDPOINT = 5


async def _saude_worker(session: AsyncSession) -> dict:
    heartbeat = (
        await session.execute(
            select(ProcessoHeartbeat.heartbeat_em).where(ProcessoHeartbeat.processo == "worker")
        )
    ).scalar_one_or_none()
    limite = datetime.now(UTC) - timedelta(seconds=LIMIAR_HEARTBEAT_WORKER_SEGUNDOS)
    online = bool(heartbeat and heartbeat >= limite)
    return {"status": "ok" if online else "indisponivel", "heartbeat_em": heartbeat}


async def _migration_atual(session: AsyncSession) -> str | None:
    return (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()


async def _erros_por_versao(session: AsyncSession) -> list[dict]:
    """Requisições e erros na janela de tempo em que cada versão esteve
    "no ar" (do próprio implantada_em até a próxima versão publicada, ou
    agora, para a mais recente) -- é o proxy mais direto disponível pra
    "erros por versão" sem instrumentar toda chamada com o id da versão
    (achado registrado como limitação também em docs/fase5, seção
    "impacto nos módulos existentes")."""
    versoes = (
        await session.execute(
            select(VersaoSistema)
            .where(VersaoSistema.status == "publicada")
            .order_by(VersaoSistema.implantada_em.desc())
            .limit(QUANTIDADE_VERSOES_PAINEL)
        )
    ).scalars().all()
    agora = datetime.now(UTC)
    resultado = []
    fim_janela = agora
    for versao in versoes:
        inicio_janela = versao.implantada_em
        metricas = (
            await session.execute(
                select(func.count(), func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0))).where(
                    EventoOperacional.criado_em >= inicio_janela, EventoOperacional.criado_em < fim_janela
                )
            )
        ).one()
        requisicoes = int(metricas[0] or 0)
        erros = int(metricas[1] or 0)
        resultado.append(
            {
                "versao_id": versao.id,
                "versao": versao.versao,
                "titulo": versao.titulo,
                "tipo_atualizacao": versao.tipo_atualizacao,
                "implantada_em": versao.implantada_em,
                "requisicoes": requisicoes,
                "erros": erros,
                "taxa_erro": round(erros / requisicoes, 4) if requisicoes else None,
            }
        )
        fim_janela = inicio_janela
    return resultado


async def _latencia_por_endpoint(session: AsyncSession) -> list[dict]:
    """Achado da Fase 6 da missão de maturidade técnica (`docs/slo-e-criterios-incidente.md`,
    seção "O que ainda não tem SLO formal"): o p95 já existia (app/api/producao.py),
    mas só agregado -- um endpoint lento isolado passa despercebido enquanto a
    média/p95 geral continua saudável. Agrupa por componente+operação nas
    últimas 24h, ordenado pelo p95 mais alto primeiro; exclui endpoints com
    poucas amostras (p95 de 2-3 chamadas não é representativo)."""
    desde = datetime.now(UTC) - timedelta(hours=24)
    linhas = (
        await session.execute(
            select(
                EventoOperacional.componente,
                EventoOperacional.operacao,
                func.count(),
                func.avg(EventoOperacional.duracao_ms),
                func.percentile_cont(0.95).within_group(EventoOperacional.duracao_ms),
                func.max(EventoOperacional.duracao_ms),
                func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0)),
            )
            .where(EventoOperacional.criado_em >= desde)
            .group_by(EventoOperacional.componente, EventoOperacional.operacao)
            .having(func.count() >= MINIMO_REQUISICOES_LATENCIA_ENDPOINT)
            .order_by(func.percentile_cont(0.95).within_group(EventoOperacional.duracao_ms).desc())
            .limit(QUANTIDADE_ENDPOINTS_LATENCIA_PAINEL)
        )
    ).all()
    return [
        {
            "componente": componente,
            "operacao": operacao,
            "requisicoes": int(requisicoes),
            "duracao_media_ms": round(float(duracao_media or 0), 1),
            "duracao_p95_ms": round(float(p95 or 0), 1),
            "duracao_max_ms": int(duracao_max or 0),
            "erros": int(erros or 0),
        }
        for componente, operacao, requisicoes, duracao_media, p95, duracao_max, erros in linhas
    ]


def _recursos_host() -> dict:
    """Indicador de infraestrutura pendente de `docs/slo-e-criterios-incidente.md`
    (CPU/memória/disco do host). Lê /proc diretamente em vez de adicionar uma
    dependência nova (psutil) só para isto -- mesmo princípio de leitura
    direta de arquivo já usado em `app/alertas_plataforma.py::_backup_mais_recente`.
    /proc/loadavg e /proc/meminfo só existem em Linux (todo ambiente real do
    projeto -- container Docker); fora disso (ex.: rodando local no Windows)
    os campos de cpu/memória voltam None em vez de quebrar o painel.
    """
    cpu: dict = {"carga_1min": None, "carga_5min": None, "carga_15min": None, "nucleos": os.cpu_count()}
    try:
        carga_1, carga_5, carga_15 = os.getloadavg()
        cpu["carga_1min"] = round(carga_1, 2)
        cpu["carga_5min"] = round(carga_5, 2)
        cpu["carga_15min"] = round(carga_15, 2)
    except OSError:
        pass

    memoria: dict = {"total_bytes": None, "disponivel_bytes": None, "percentual_uso": None}
    try:
        valores = {}
        with open("/proc/meminfo", encoding="ascii") as arquivo:
            for linha in arquivo:
                chave, _, resto = linha.partition(":")
                if chave in ("MemTotal", "MemAvailable"):
                    valores[chave] = int(resto.strip().split()[0]) * 1024
        if valores.get("MemTotal"):
            total = valores["MemTotal"]
            disponivel = valores.get("MemAvailable", 0)
            memoria = {
                "total_bytes": total,
                "disponivel_bytes": disponivel,
                "percentual_uso": round((1 - disponivel / total) * 100, 1),
            }
    except OSError:
        pass

    uso_disco = shutil.disk_usage("/")
    disco = {
        "total_bytes": uso_disco.total,
        "usado_bytes": uso_disco.used,
        "percentual_uso": round(uso_disco.used / uso_disco.total * 100, 1) if uso_disco.total else None,
    }

    return {"cpu": cpu, "memoria": memoria, "disco": disco}


@router.get("/observabilidade/painel-tecnico")
async def painel_tecnico(session: SessionDep, usuario: TechDep) -> dict:
    _exigir_acesso_tech(usuario)
    settings = get_settings()
    agora = datetime.now(UTC)

    processo_comum = {"versao": settings.app_version, "commit": settings.git_sha}
    saude_worker = await _saude_worker(session)
    estado_rpi = await session.get(RpiSyncEstado, 1)
    limite_rpi = agora - timedelta(seconds=max(60, settings.rpi_sync_poll_seconds * 3))
    rpi_online = bool(estado_rpi and estado_rpi.heartbeat_em and estado_rpi.heartbeat_em >= limite_rpi)

    flags = (
        await session.execute(select(FeatureFlag).where(FeatureFlag.ativo.is_(True)).order_by(FeatureFlag.codigo))
    ).scalars().all()

    desde_organizacoes = agora - timedelta(hours=24)
    linhas_organizacoes = (
        await session.execute(
            select(
                FeatureFlagEvento.organizacao_id,
                Organizacao.nome,
                FeatureFlag.codigo,
                func.count(),
            )
            .join(Organizacao, Organizacao.id == FeatureFlagEvento.organizacao_id)
            .join(FeatureFlag, FeatureFlag.id == FeatureFlagEvento.feature_flag_id)
            .where(
                FeatureFlagEvento.tipo.in_(("erro", "falha_integracao")),
                FeatureFlagEvento.criado_em >= desde_organizacoes,
            )
            .group_by(FeatureFlagEvento.organizacao_id, Organizacao.nome, FeatureFlag.codigo)
        )
    ).all()
    organizacoes_afetadas: dict[int, dict] = {}
    for organizacao_id, nome, codigo_flag, total in linhas_organizacoes:
        item = organizacoes_afetadas.setdefault(
            organizacao_id, {"organizacao_id": organizacao_id, "organizacao_nome": nome, "eventos": 0, "flags": []}
        )
        item["eventos"] += total
        item["flags"].append({"codigo": codigo_flag, "eventos": total})

    ultimo_deploy_item = (
        await session.execute(
            select(VersaoSistema)
            .where(VersaoSistema.status == "publicada")
            .order_by(VersaoSistema.implantada_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return {
        "gerado_em": agora,
        "processos": {
            "api": {**processo_comum, "status": "ok"},
            "worker": {**processo_comum, **saude_worker},
            "rpi_sync": {
                **processo_comum,
                "status": "ok" if rpi_online else "indisponivel",
                "heartbeat_em": estado_rpi.heartbeat_em if estado_rpi else None,
            },
        },
        "migration_atual": await _migration_atual(session),
        "erros_por_versao": await _erros_por_versao(session),
        "latencia_por_endpoint": await _latencia_por_endpoint(session),
        "recursos_host": _recursos_host(),
        "feature_flags_ativas": [
            {
                "codigo": flag.codigo,
                "nome": flag.nome,
                "estado_padrao": flag.estado_padrao,
                "estagio_rollout": flag.estagio_rollout,
                "percentual_rollout": flag.percentual_rollout,
                "pausado_em": flag.pausado_em,
                "pausado_motivo": flag.pausado_motivo,
            }
            for flag in flags
        ],
        "organizacoes_afetadas": sorted(organizacoes_afetadas.values(), key=lambda item: -item["eventos"]),
        "ultimo_deploy": (
            {
                "versao_id": ultimo_deploy_item.id,
                "versao": ultimo_deploy_item.versao,
                "titulo": ultimo_deploy_item.titulo,
                "tipo_atualizacao": ultimo_deploy_item.tipo_atualizacao,
                "commit_sha": ultimo_deploy_item.commit_sha,
                "migration_revision": ultimo_deploy_item.migration_revision,
                "implantada_em": ultimo_deploy_item.implantada_em,
                "publicado_por": ultimo_deploy_item.publicado_por,
                "evidencias_aprovadas": sum(
                    1 for ev in (ultimo_deploy_item.evidencias_testes or []) if ev.get("resultado") == "aprovado"
                ),
                "evidencias_total": len(ultimo_deploy_item.evidencias_testes or []),
            }
            if ultimo_deploy_item
            else None
        ),
    }


@router.post("/feature-flags/{codigo}/desligar")
async def desligar_flag_imediatamente(codigo: str, session: SessionDep, usuario: TechDep) -> dict:
    """Fase 7, critério de rollback: "feature flag pode ser desligada
    imediatamente". Kill-switch direto (FeatureFlag.ativo=False, já
    existente desde a Fase 4) -- diferente de interromper_rollout (Fase 5,
    recua um estágio), esta ação corta a flag para TODAS as organizações
    de uma vez, sem exigir motivo (é a ação de emergência: já existe
    /interromper para quando dá tempo de registrar o porquê)."""
    _exigir_acesso_tech(usuario)
    flag = await obter_flag(session, codigo)
    if flag is None:
        raise HTTPException(404, "Feature flag não encontrada")
    if not flag.ativo:
        return {"codigo": flag.codigo, "ativo": False, "ja_estava_desligada": True}
    flag.ativo = False
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="FLAG_DESLIGAR",
            recurso=f"feature_flag:{flag.id}",
            sucesso=True,
            status_http=200,
            detalhes={"codigo": flag.codigo, "origem": "painel_tecnico"},
        )
    )
    await session.commit()
    return {"codigo": flag.codigo, "ativo": False, "ja_estava_desligada": False}


@router.post("/feature-flags/{codigo}/religar")
async def religar_flag(codigo: str, session: SessionDep, usuario: TechDep) -> dict:
    """Contrapartida de /desligar -- sem isso, o kill-switch de emergência
    não tinha volta pela API (achado ao verificar /desligar ao vivo em
    produção logo após implementar: precisou de UPDATE manual no banco pra
    desfazer). Reativa a flag; estágio/percentual continuam exatamente
    como estavam antes de desligar (não readianta nada sozinho)."""
    _exigir_acesso_tech(usuario)
    flag = await obter_flag(session, codigo)
    if flag is None:
        raise HTTPException(404, "Feature flag não encontrada")
    if flag.ativo:
        return {"codigo": flag.codigo, "ativo": True, "ja_estava_ligada": True}
    flag.ativo = True
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="FLAG_RELIGAR",
            recurso=f"feature_flag:{flag.id}",
            sucesso=True,
            status_http=200,
            detalhes={"codigo": flag.codigo, "origem": "painel_tecnico"},
        )
    )
    await session.commit()
    return {"codigo": flag.codigo, "ativo": True, "ja_estava_ligada": False}

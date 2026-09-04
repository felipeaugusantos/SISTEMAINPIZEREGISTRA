"""Alertas de infraestrutura/plataforma (achado FASE6-9 da auditoria, 04/09/2026).

AlertaSistema já existia (app/models.py), mas só era usado com organizacao_id
preenchido (alertas de uma organização específica: trial expirado, retenção
pendente etc). organizacao_id é nullable -- aqui reaproveitamos isso para
representar um alerta de plataforma inteira (fila de falhas, RPI
desatualizada, backup ausente, erro de migration, latência/erro de API),
sem inventar uma tabela nova.

Dedup: só um alerta ABERTO por código de cada vez (mesmo padrão que
RETENCAO_PENDENTE já usava em app/worker.py) -- evita reenviar e-mail e
recriar linha a cada ciclo de manutenção enquanto o problema persiste.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.emailing import enviar_alerta_plataforma
from app.models import AlertaSistema, EventoOperacional, RpiImportacao, RpiSyncEstado
from app.queueing import status_fila
from app.rpi.health import avaliar_saude_rpi
from app.settings import get_settings


async def registrar_alerta_plataforma(
    session: AsyncSession,
    *,
    codigo: str,
    severidade: str,
    mensagem: str,
    detalhes: dict | None = None,
) -> None:
    """Cria um AlertaSistema de plataforma (organizacao_id=None) e dispara o
    e-mail de reforço -- só se não houver um já aberto com o mesmo código.
    Não faz commit (quem chama já está numa transação de manutenção)."""
    existente = (
        await session.execute(
            select(AlertaSistema.id).where(
                AlertaSistema.organizacao_id.is_(None),
                AlertaSistema.codigo == codigo,
                AlertaSistema.resolvido_em.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existente is not None:
        return
    session.add(
        AlertaSistema(
            organizacao_id=None,
            severidade=severidade,
            codigo=codigo,
            mensagem=mensagem,
            detalhes=detalhes or {},
        )
    )
    await enviar_alerta_plataforma(codigo, severidade, mensagem)


async def resolver_alerta_plataforma(session: AsyncSession, *, codigo: str) -> None:
    """Marca como resolvido qualquer alerta de plataforma aberto com esse
    código -- chamado quando a checagem seguinte encontra a situação normal
    de novo (ex: fila de falhas voltou a zero)."""
    abertos = (
        await session.execute(
            select(AlertaSistema).where(
                AlertaSistema.organizacao_id.is_(None),
                AlertaSistema.codigo == codigo,
                AlertaSistema.resolvido_em.is_(None),
            )
        )
    ).scalars()
    agora = datetime.now(UTC)
    for alerta in abertos:
        alerta.resolvido_em = agora


async def verificar_saude_plataforma(session: AsyncSession) -> None:
    """Tarefa de manutenção "plataforma.verificar_saude" (achado FASE6-9,
    04/09/2026) -- roda a cada ciclo horário de _loop_manutencao
    (app/worker.py) e cobre os alertas que antes só existiam como número
    num painel (ninguém era avisado proativamente):

    - 9a) fila de falhas do worker (FAILED_KEY);
    - 9b) RPI desatualizada (mesmo cálculo de GET /health/rpi);
    - 9f) taxa de erro / latência média da API nas últimas 24h.

    9d (backup ausente) e 9g (SMTP/IMAP) não entram aqui -- 9d exige acesso
    ao diretório de backups do host, que hoje não é montado no container do
    worker (decisão registrada para tratar à parte); 9g já é verificado nos
    próprios pontos de envio (app/emailing.py, app/imap_polling.py).
    """
    settings = get_settings()

    fila = await status_fila()
    if fila.get("status") != "ok":
        await registrar_alerta_plataforma(
            session,
            codigo="FILA_INDISPONIVEL",
            severidade="critico",
            mensagem=f"Redis/fila de jobs indisponível: {fila.get('erro', 'motivo desconhecido')}.",
            detalhes={"erro": fila.get("erro")},
        )
    else:
        await resolver_alerta_plataforma(session, codigo="FILA_INDISPONIVEL")
        falhas = int(fila.get("falhas") or 0)
        if falhas >= settings.alerta_fila_falhas_limite:
            await registrar_alerta_plataforma(
                session,
                codigo="FILA_FALHAS_ALTA",
                severidade="critico" if falhas >= settings.alerta_fila_falhas_limite * 4 else "aviso",
                mensagem=(
                    f"{falhas} job(s) esgotaram as tentativas e estão na fila de falhas do worker "
                    f"(limite configurado: {settings.alerta_fila_falhas_limite})."
                ),
                detalhes={"falhas": falhas},
            )
        else:
            await resolver_alerta_plataforma(session, codigo="FILA_FALHAS_ALTA")

    estado_rpi = await session.get(RpiSyncEstado, 1)
    ultima_rpi = (
        await session.execute(
            select(RpiImportacao)
            .where(RpiImportacao.tipo == "marca")
            .order_by(RpiImportacao.numero_rpi.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    ultima_sincronizacao = (
        estado_rpi.ultima_verificacao_em if estado_rpi and estado_rpi.ultima_verificacao_em else None
    ) or (ultima_rpi.importado_em if ultima_rpi else None)
    status_rpi, idade_horas, motivos = avaliar_saude_rpi(
        status_sync=estado_rpi.status if estado_rpi else None,
        ultima_rpi_oficial=estado_rpi.ultima_rpi_oficial if estado_rpi else None,
        ultima_rpi_importada=ultima_rpi.numero_rpi if ultima_rpi else None,
        ultima_sincronizacao=ultima_sincronizacao,
        status_integridade=ultima_rpi.status_integridade if ultima_rpi else None,
        limite_atraso_horas=settings.rpi_stale_hours,
    )
    # "processando" é uma sincronização em andamento -- estado transitório
    # normal, não é alertado nem tratado como resolução do que já estava
    # aberto (ainda não sabemos se vai terminar bem).
    if status_rpi in ("erro", "atrasado"):
        await registrar_alerta_plataforma(
            session,
            codigo="RPI_DESATUALIZADA",
            severidade="critico" if status_rpi == "erro" else "aviso",
            mensagem=(
                f"Sincronização da RPI está \"{status_rpi}\" "
                f"({'; '.join(motivos) if motivos else 'sem motivo detalhado'})."
            ),
            detalhes={"status": status_rpi, "idade_horas": idade_horas, "motivos": motivos},
        )
    elif status_rpi == "ok":
        await resolver_alerta_plataforma(session, codigo="RPI_DESATUALIZADA")

    desde = datetime.now(UTC) - timedelta(hours=24)
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
    duracao_media_ms = float(metricas[2] or 0)
    taxa_erro = erros / requisicoes if requisicoes else 0.0
    if requisicoes >= 20 and (
        taxa_erro >= settings.alerta_api_taxa_erro_limite
        or duracao_media_ms >= settings.alerta_api_latencia_media_ms_limite
    ):
        await registrar_alerta_plataforma(
            session,
            codigo="API_LATENCIA_ERRO_ALTA",
            severidade="aviso",
            mensagem=(
                f"Nas últimas 24h: {requisicoes} requisições, taxa de erro {taxa_erro:.1%} "
                f"(limite {settings.alerta_api_taxa_erro_limite:.0%}), latência média "
                f"{duracao_media_ms:.0f}ms (limite {settings.alerta_api_latencia_media_ms_limite:.0f}ms)."
            ),
            detalhes={"requisicoes": requisicoes, "erros": erros, "duracao_media_ms": duracao_media_ms},
        )
    else:
        await resolver_alerta_plataforma(session, codigo="API_LATENCIA_ERRO_ALTA")

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.juridico import executar_motor_organizacao
from app.cadencia_email import processar_envios_cadencia_pendentes
from app.cli.sincronizar_alto_renome import sincronizar as sincronizar_alto_renome
from app.database import session_factory
from app.emailing import enviar_alerta_atividades_atrasadas
from app.imap_polling import verificar_respostas_email
from app.models import (
    AlertaSistema,
    Lead,
    LembreteCRM,
    Movimentacao,
    Organizacao,
    Processo,
    ProcessoMonitorado,
    RenovacaoFinanceira,
    StatusLead,
    UsuarioOperacoes,
)
from app.queueing import (
    FAILED_KEY,
    MAX_ATTEMPTS,
    METRICS_KEY,
    PROCESSING_KEY,
    QUEUE_KEY,
    agendar_retry,
    cliente_redis,
    promover_retentativas,
)
from app.request_context import definir_request_id, request_id_atual, restaurar_request_id
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant
from app.trademarks.agent import reconciliar_resultados_reais, reprocessar_agentes_pendentes
from app.trademarks.learning import (
    executar_pipeline_aprendizado,
    reprocessar_previsoes_pendentes,
)
from app.trademarks.model_status import StatusModelo
from app.trademarks.status import normalizar_despacho

logger = logging.getLogger("ze_registra.worker")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


def _somar_anos(referencia: date, anos: int) -> date:
    try:
        return referencia.replace(year=referencia.year + anos)
    except ValueError:
        # 29 de fevereiro em ano nao bissexto -> usa 28/02 do ano de destino.
        return referencia.replace(year=referencia.year + anos, day=28)


async def processar(tipo: str, payload: dict) -> None:
    async with session_factory() as session:
        await aplicar_contexto_tenant(session, 1, superadmin=True)
        if tipo == "assinaturas.verificar":
            agora = datetime.now(UTC)
            orgs = (
                await session.execute(
                    select(Organizacao).where(
                        Organizacao.status == "trial",
                        Organizacao.trial_ate < agora,
                        Organizacao.suspender_automaticamente.is_(True),
                    )
                )
            ).scalars()
            for org in orgs:
                org.status, org.assinatura_status = "suspensa", "trial_expirado"
                session.add(
                    AlertaSistema(
                        organizacao_id=org.id,
                        severidade="aviso",
                        codigo="TRIAL_EXPIRADO",
                        mensagem="Trial expirado; acesso suspenso automaticamente.",
                    )
                )
        elif tipo == "privacidade.verificar_retencao":
            orgs = (await session.execute(select(Organizacao))).scalars()
            agora = datetime.now(UTC)
            for org in orgs:
                limite = agora - timedelta(days=org.retencao_dados_dias)
                total = len(
                    list(
                        (
                            await session.execute(
                                select(Lead.id).where(Lead.organizacao_id == org.id, Lead.criado_em < limite)
                            )
                        ).scalars()
                    )
                )
                if total:
                    existente = (
                        await session.execute(
                            select(AlertaSistema.id).where(
                                AlertaSistema.organizacao_id == org.id,
                                AlertaSistema.codigo == "RETENCAO_PENDENTE",
                                AlertaSistema.resolvido_em.is_(None),
                            )
                        )
                    ).scalar_one_or_none()
                    if not existente:
                        session.add(
                            AlertaSistema(
                                organizacao_id=org.id,
                                severidade="aviso",
                                codigo="RETENCAO_PENDENTE",
                                mensagem=(f"{total} lead(s) excedem a política de retenção e aguardam revisão humana."),
                                detalhes={"total": total},
                            )
                        )
        elif tipo == "crm.reengajamento_inatividade":
            # Achado da auditoria do CRM: a política de "próxima ação obrigatória"
            # (app/crm.py::aplicar_politica_oportunidade) só é aplicada quando
            # alguém mexe no lead -- sozinho, um lead esquecido continua esquecido
            # para sempre. Este job varre periodicamente e cria um lembrete para
            # o operador retomar contato. Idempotente por semana ISO: no máximo
            # um lembrete de reengajamento por lead por semana, mesmo rodando de
            # hora em hora.
            agora = datetime.now(UTC)
            semana = agora.strftime("%G-W%V")
            leads = (
                await session.execute(
                    select(Lead)
                    .join(Organizacao, Organizacao.id == Lead.organizacao_id)
                    .where(
                        Organizacao.status != "suspensa",
                        Lead.status.notin_([StatusLead.CONVERTIDO, StatusLead.DESCARTADO]),
                        Lead.arquivado_em.is_(None),
                        or_(Lead.proxima_acao_em.is_(None), Lead.proxima_acao_em < agora),
                    )
                )
            ).scalars()
            criados = 0
            atrasados_por_responsavel: dict[int, list[Lead]] = {}
            for lead in leads:
                inserido = (
                    await session.execute(
                        pg_insert(LembreteCRM)
                        .values(
                            organizacao_id=lead.organizacao_id,
                            lead_id=lead.id,
                            responsavel_id=lead.responsavel_id,
                            tipo="retorno",
                            prioridade="alta",
                            titulo="Oportunidade parada — retomar contato",
                            descricao=(
                                "Sem próxima ação definida ou o prazo já venceu. "
                                "Verifique o andamento e planeje o próximo passo."
                            ),
                            lembrar_em=agora,
                            status="pendente",
                            criado_por="Automação (reengajamento por inatividade)",
                            criado_por_id=None,
                            idempotency_key=f"reengajamento:{lead.id}:{semana}",
                        )
                        .on_conflict_do_nothing(constraint="uq_lembrete_crm_idempotencia")
                        .returning(LembreteCRM.id)
                    )
                ).scalar_one_or_none()
                if inserido is not None:
                    criados += 1
                    if lead.responsavel_id is not None:
                        atrasados_por_responsavel.setdefault(lead.responsavel_id, []).append(lead)
            # Achado P2 da auditoria de Leads: nenhuma notificação ativa avisava
            # o responsável de uma atividade atrasada -- só aparecia se ele
            # entrasse no sistema. Um e-mail por responsável, no máximo uma vez
            # por semana por lead (mesma idempotência do lembrete acima).
            if atrasados_por_responsavel:
                linhas_operadores = (
                    await session.execute(
                        select(UsuarioOperacoes.id, UsuarioOperacoes.nome, UsuarioOperacoes.email).where(
                            UsuarioOperacoes.id.in_(atrasados_por_responsavel.keys())
                        )
                    )
                ).all()
                operadores = {row[0]: (row[1], row[2]) for row in linhas_operadores}
                for responsavel_id, leads_atrasados in atrasados_por_responsavel.items():
                    operador = operadores.get(responsavel_id)
                    if operador is None:
                        continue
                    nome, email = operador
                    await enviar_alerta_atividades_atrasadas(
                        nome, email, [(item.nome, item.marca) for item in leads_atrasados]
                    )
            if criados:
                session.add(
                    AlertaSistema(
                        organizacao_id=1,
                        severidade="info",
                        codigo="REENGAJAMENTO_CRM_EXECUTADO",
                        mensagem=f"Reengajamento por inatividade: {criados} lembrete(s) criado(s).",
                        detalhes={"criados": criados, "semana": semana},
                    )
                )
        elif tipo == "crm.gerar_renovacoes_marca":
            # Achado da auditoria: RenovacaoFinanceira e o endpoint de criar ja
            # existiam (app/api/contratacoes.py), mas so eram usados manualmente --
            # nada gerava a renovacao sozinho quando a marca era concedida. Gatilho
            # confiavel: Processo.situacao_normalizada == "registrada" (concessao de
            # registro, ja calculado pelo importador da RPI). Vencimento = data da
            # concessao (extraida da movimentacao que causou a mudanca de situacao)
            # + 10 anos, prazo padrao de vigencia do registro de marca no Brasil.
            pendentes = (
                await session.execute(
                    select(ProcessoMonitorado.organizacao_id, ProcessoMonitorado.processo_id)
                    .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
                    .where(
                        Processo.situacao_normalizada == "registrada",
                        ~exists(
                            select(1).where(
                                RenovacaoFinanceira.organizacao_id == ProcessoMonitorado.organizacao_id,
                                RenovacaoFinanceira.processo_id == ProcessoMonitorado.processo_id,
                                RenovacaoFinanceira.tipo == "renovacao",
                            )
                        ),
                    )
                    .distinct()
                )
            ).all()
            criadas = 0
            for organizacao_id, processo_id in pendentes:
                movimentacoes = (
                    await session.execute(
                        select(Movimentacao)
                        .where(Movimentacao.processo_id == processo_id)
                        .order_by(Movimentacao.data_rpi.desc())
                    )
                ).scalars()
                data_concessao = next(
                    (
                        mov.data_rpi
                        for mov in movimentacoes
                        if normalizar_despacho(mov.codigo_despacho, mov.descricao).codigo == "registrada"
                    ),
                    None,
                )
                if data_concessao is None:
                    continue
                inserido = (
                    await session.execute(
                        pg_insert(RenovacaoFinanceira)
                        .values(
                            organizacao_id=organizacao_id,
                            processo_id=processo_id,
                            tipo="renovacao",
                            referencia=f"registro-{data_concessao.isoformat()}",
                            vencimento=_somar_anos(data_concessao, 10),
                            status="pendente",
                        )
                        .on_conflict_do_nothing(constraint="uq_renovacao_financeira")
                        .returning(RenovacaoFinanceira.id)
                    )
                ).scalar_one_or_none()
                if inserido is not None:
                    criadas += 1
            if criadas:
                session.add(
                    AlertaSistema(
                        organizacao_id=1,
                        severidade="info",
                        codigo="RENOVACOES_GERADAS",
                        mensagem=f"{criadas} renovação(ões) de marca gerada(s) automaticamente.",
                        detalhes={"criadas": criadas},
                    )
                )
        elif tipo == "registrabilidade.reconciliar_resultados":
            await reconciliar_resultados_reais(session)
        elif tipo == "registrabilidade.reprocessar_previsoes":
            resultado = await reprocessar_previsoes_pendentes(
                session,
                organizacao_id=payload.get("organizacao_id"),
            )
            sem_modelo = resultado["status"] == "sem_modelo_ativo"
            session.add(
                AlertaSistema(
                    organizacao_id=payload.get("organizacao_id") or 1,
                    severidade="aviso" if sem_modelo else "info",
                    codigo="PREVISOES_REPROCESSADAS",
                    mensagem=(
                        "Nenhuma previsão foi criada: não existe modelo supervisionado ativo."
                        if sem_modelo
                        else (f"Reprocessamento concluído: {resultado['processadas']} previsão(ões) criada(s).")
                    ),
                    detalhes=resultado,
                )
            )
        elif tipo == "registrabilidade.reprocessar_agentes":
            resultado = await reprocessar_agentes_pendentes(
                session,
                organizacao_id=payload.get("organizacao_id"),
            )
            session.add(
                AlertaSistema(
                    organizacao_id=payload.get("organizacao_id") or 1,
                    severidade="info",
                    codigo="AGENTES_REPROCESSADOS",
                    mensagem=(f"Agentes atualizados: {resultado['processadas']} pesquisa(s) processada(s)."),
                    detalhes=resultado,
                )
            )
        elif tipo == "registrabilidade.pipeline_aprendizado":
            resultado = await executar_pipeline_aprendizado(
                session,
                administrador=payload.get("solicitado_por") or "worker",
                limite_dataset=int(payload.get("limite_dataset") or 3000),
            )
            aguardando_revisoes = resultado["modelo_status"] == StatusModelo.VALIDATION.value and any(
                "Revisões humanas insuficientes" in bloqueio for bloqueio in resultado["bloqueios"]
            )
            session.add(
                AlertaSistema(
                    organizacao_id=payload.get("organizacao_id") or 1,
                    severidade="info" if resultado["ativado"] else "aviso",
                    codigo=(
                        "MODELO_APRENDIZADO_ATIVADO"
                        if resultado["ativado"]
                        else (
                            "MODELO_APRENDIZADO_AGUARDANDO_REVISOES"
                            if aguardando_revisoes
                            else "MODELO_APRENDIZADO_BLOQUEADO"
                        )
                    ),
                    mensagem=(
                        (
                            f"Modelo em VALIDATION; "
                            f"{resultado['reprocessamento']['processadas']} previsão(ões) "
                            "interna(s) preparada(s). Ativação bloqueada: " + "; ".join(resultado["bloqueios"])
                        )
                        if aguardando_revisoes
                        else (
                            f"Pipeline concluído com o modelo {resultado['modelo_versao']}: "
                            f"{resultado['modelo_status']}."
                        )
                    ),
                    detalhes=resultado,
                )
            )
        elif tipo == "registrabilidade.ativar_candidato":
            # Compatibilidade com tarefas antigas já enfileiradas. Promoções agora exigem
            # ação autenticada e explícita no endpoint administrativo.
            session.add(
                AlertaSistema(
                    organizacao_id=payload.get("organizacao_id") or 1,
                    severidade="aviso",
                    codigo="ATIVACAO_AUTOMATICA_MODELO_IGNORADA",
                    mensagem="Ativação automática ignorada; use a promoção explícita VALIDATION → ACTIVE.",
                    detalhes={"solicitado_por": payload.get("solicitado_por")},
                )
            )
        elif tipo == "alto_renome.sincronizar":
            await sincronizar_alto_renome(get_settings().alto_renome_page_url)
        elif tipo == "juridico.executar_motor":
            organizacoes = (
                await session.execute(select(Organizacao.id).where(Organizacao.status != "suspensa"))
            ).scalars()
            for organizacao_id in organizacoes:
                await executar_motor_organizacao(session, organizacao_id)
        elif tipo == "cadencia.enviar_emails_pendentes":
            resultado = await processar_envios_cadencia_pendentes(session)
            if resultado.get("enviados") or resultado.get("falhas"):
                session.add(
                    AlertaSistema(
                        organizacao_id=1,
                        severidade="aviso" if resultado.get("falhas") else "info",
                        codigo="CADENCIA_EMAILS_PROCESSADOS",
                        mensagem=(
                            f"{resultado.get('enviados', 0)} e-mail(s) de cadência enviado(s), "
                            f"{resultado.get('falhas', 0)} falha(s)."
                        ),
                        detalhes=resultado,
                    )
                )
        elif tipo == "cadencia.verificar_respostas_email":
            resultado = await verificar_respostas_email(session)
            if resultado.get("pausados"):
                session.add(
                    AlertaSistema(
                        organizacao_id=1,
                        severidade="info",
                        codigo="CADENCIA_PAUSADA_POR_RESPOSTA",
                        mensagem=f"{resultado['pausados']} envio(s) de cadência pausado(s) por resposta do titular.",
                        detalhes=resultado,
                    )
                )
        elif tipo == "vigilancia.executar_semanal":
            from app.vigilancia import executar_vigilancia_semanal

            resultado = await executar_vigilancia_semanal(session, payload.get("organizacao_id"))
            session.add(
                AlertaSistema(
                    organizacao_id=payload.get("organizacao_id") or 1,
                    severidade="info",
                    codigo="VIGILANCIA_SEMANAL_CONCLUIDA",
                    mensagem=f"Vigilancia semanal concluida: {resultado['criadas']} colidencia(s) nova(s).",
                    detalhes=resultado,
                )
            )
        else:
            raise ValueError(f"Tipo de trabalho desconhecido: {tipo}")
        await session.commit()


async def processar_rastreado(tipo: str, payload: dict, request_id: str | None = None) -> None:
    token_contexto = definir_request_id(request_id)
    request_id_valor = request_id_atual()
    inicio = datetime.now(UTC)
    try:
        await processar(tipo, payload)
        logger.info(
            json.dumps(
                {
                    "event": "WORKER_JOB_COMPLETED",
                    "request_id": request_id_valor,
                    "job_type": tipo,
                    "duration_ms": round((datetime.now(UTC) - inicio).total_seconds() * 1000),
                },
                ensure_ascii=False,
            )
        )
    finally:
        restaurar_request_id(token_contexto)


async def main() -> None:
    redis = cliente_redis()
    # Recupera trabalhos que ficaram em processamento apos encerramento abrupto.
    while await redis.llen(PROCESSING_KEY):
        bruto_pendente = await redis.rpop(PROCESSING_KEY)
        if bruto_pendente:
            await redis.lpush(QUEUE_KEY, bruto_pendente)
    proxima_manutencao = datetime.now(UTC)
    proximo_alto_renome = datetime.now(UTC)
    while True:
        await promover_retentativas(redis)
        bruto = await redis.brpoplpush(QUEUE_KEY, PROCESSING_KEY, timeout=5)
        if not bruto:
            if datetime.now(UTC) >= proxima_manutencao:
                for tarefa in (
                    "assinaturas.verificar",
                    "privacidade.verificar_retencao",
                    "crm.reengajamento_inatividade",
                    "crm.gerar_renovacoes_marca",
                    "cadencia.enviar_emails_pendentes",
                    "cadencia.verificar_respostas_email",
                    "registrabilidade.reconciliar_resultados",
                    "juridico.executar_motor",
                    "vigilancia.executar_semanal",
                ):
                    try:
                        await processar_rastreado(tarefa, {})
                    except Exception as exc:
                        await redis.rpush(
                            FAILED_KEY,
                            json.dumps(
                                {
                                    "job": tarefa,
                                    "erro": str(exc),
                                    "falhou_em": datetime.now(UTC).isoformat(),
                                }
                            ),
                        )
                proxima_manutencao = datetime.now(UTC) + timedelta(hours=1)
            if datetime.now(UTC) >= proximo_alto_renome:
                try:
                    await processar_rastreado("alto_renome.sincronizar", {})
                except Exception as exc:
                    await redis.rpush(
                        FAILED_KEY,
                        json.dumps(
                            {
                                "job": "alto_renome.sincronizar",
                                "erro": type(exc).__name__,
                                "falhou_em": datetime.now(UTC).isoformat(),
                            }
                        ),
                    )
                proximo_alto_renome = datetime.now(UTC) + timedelta(days=7)
            continue
        try:
            job = json.loads(bruto)
            await processar_rastreado(job["tipo"], job.get("payload", {}), job.get("request_id"))
            await redis.lrem(PROCESSING_KEY, 1, bruto)
            await redis.hincrby(METRICS_KEY, "concluidos", 1)
        except Exception as exc:
            await redis.lrem(PROCESSING_KEY, 1, bruto)
            try:
                job = json.loads(bruto)
            except (TypeError, json.JSONDecodeError):
                job = {"id": "invalido", "tipo": "desconhecido", "payload": {}}
            job["tentativas"] = int(job.get("tentativas", 0)) + 1
            job["ultimo_erro"] = type(exc).__name__
            job["ultima_falha_em"] = datetime.now(UTC).isoformat()
            logger.exception(
                json.dumps(
                    {
                        "event": "WORKER_JOB_FAILED",
                        "request_id": job.get("request_id"),
                        "job_id": job.get("id"),
                        "job_type": job.get("tipo"),
                        "attempt": job["tentativas"],
                        "error": type(exc).__name__,
                    },
                    ensure_ascii=False,
                )
            )
            if job["tentativas"] < MAX_ATTEMPTS:
                await agendar_retry(redis, job)
            else:
                await redis.rpush(FAILED_KEY, json.dumps(job))
                await redis.hincrby(METRICS_KEY, "falhas", 1)


if __name__ == "__main__":
    asyncio.run(main())

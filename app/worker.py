import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.juridico import executar_motor_organizacao
from app.cli.sincronizar_alto_renome import sincronizar as sincronizar_alto_renome
from app.database import session_factory
from app.models import AlertaSistema, Lead, ModeloRegistrabilidade, Organizacao
from app.queueing import FAILED_KEY, MAX_ATTEMPTS, PROCESSING_KEY, QUEUE_KEY, cliente_redis
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant
from app.trademarks.agent import reconciliar_resultados_reais, reprocessar_agentes_pendentes
from app.trademarks.learning import (
    ativar_modelo,
    executar_pipeline_aprendizado,
    reprocessar_previsoes_pendentes,
)


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
                                select(Lead.id).where(
                                    Lead.organizacao_id == org.id, Lead.criado_em < limite
                                )
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
                                mensagem=(
                                    f"{total} lead(s) excedem a política de retenção "
                                    "e aguardam revisão humana."
                                ),
                                detalhes={"total": total},
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
                        "Nenhuma previsão foi criada: não existe modelo "
                        "supervisionado ativo."
                        if sem_modelo
                        else (
                            f"Reprocessamento concluído: {resultado['processadas']} "
                            "previsão(ões) criada(s)."
                        )
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
                    mensagem=(
                        f"Agentes atualizados: {resultado['processadas']} pesquisa(s) processada(s)."
                    ),
                    detalhes=resultado,
                )
            )
        elif tipo == "registrabilidade.pipeline_aprendizado":
            resultado = await executar_pipeline_aprendizado(
                session,
                administrador=payload.get("solicitado_por") or "worker",
                limite_dataset=int(payload.get("limite_dataset") or 3000),
            )
            aguardando_revisoes = (
                resultado["modelo_status"] == "candidato"
                and any(
                    "Revisões humanas insuficientes" in bloqueio
                    for bloqueio in resultado["bloqueios"]
                )
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
                            else "MODELO_APRENDIZADO_REPROVADO"
                        )
                    ),
                    mensagem=(
                        (
                            f"Modelo candidato criado em modo sombra; "
                            f"{resultado['reprocessamento']['processadas']} previsão(ões) "
                            "interna(s) preparada(s). Ativação bloqueada: "
                            + "; ".join(resultado["bloqueios"])
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
            modelo = (
                await session.execute(
                    select(ModeloRegistrabilidade)
                    .where(ModeloRegistrabilidade.status == "candidato")
                    .order_by(ModeloRegistrabilidade.treinado_em.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if modelo is not None:
                try:
                    reprocessamento = await ativar_modelo(
                        session,
                        modelo,
                        payload.get("solicitado_por") or "ativacao-automatica",
                    )
                except ValueError:
                    pass
                else:
                    session.add(
                        AlertaSistema(
                            organizacao_id=payload.get("organizacao_id") or 1,
                            severidade="info",
                            codigo="MODELO_APRENDIZADO_ATIVADO",
                            mensagem=(
                                f"Modelo {modelo.versao} ativado automaticamente; "
                                f"{reprocessamento['processadas']} previsão(ões) reprocessada(s)."
                            ),
                            detalhes=reprocessamento,
                        )
                    )
        elif tipo == "alto_renome.sincronizar":
            await sincronizar_alto_renome(get_settings().alto_renome_page_url)
        elif tipo == "juridico.executar_motor":
            organizacoes = (
                await session.execute(
                    select(Organizacao.id).where(Organizacao.status != "suspensa")
                )
            ).scalars()
            for organizacao_id in organizacoes:
                await executar_motor_organizacao(session, organizacao_id)
        else:
            raise ValueError(f"Tipo de trabalho desconhecido: {tipo}")
        await session.commit()


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
        bruto = await redis.brpoplpush(QUEUE_KEY, PROCESSING_KEY, timeout=5)
        if not bruto:
            if datetime.now(UTC) >= proxima_manutencao:
                for tarefa in (
                    "assinaturas.verificar",
                    "privacidade.verificar_retencao",
                    "registrabilidade.reconciliar_resultados",
                    "juridico.executar_motor",
                ):
                    try:
                        await processar(tarefa, {})
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
                    await processar("alto_renome.sincronizar", {})
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
            await processar(job["tipo"], job.get("payload", {}))
            await redis.lrem(PROCESSING_KEY, 1, bruto)
        except Exception as exc:
            await redis.lrem(PROCESSING_KEY, 1, bruto)
            try:
                job = json.loads(bruto)
            except (TypeError, json.JSONDecodeError):
                job = {"id": "invalido", "tipo": "desconhecido", "payload": {}}
            job["tentativas"] = int(job.get("tentativas", 0)) + 1
            job["ultimo_erro"] = type(exc).__name__
            job["ultima_falha_em"] = datetime.now(UTC).isoformat()
            if job["tentativas"] < MAX_ATTEMPTS:
                await redis.rpush(QUEUE_KEY, json.dumps(job))
            else:
                await redis.rpush(FAILED_KEY, json.dumps(job))


if __name__ == "__main__":
    asyncio.run(main())

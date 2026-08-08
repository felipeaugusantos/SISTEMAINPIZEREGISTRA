import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.database import session_factory
from app.models import AlertaSistema, Lead, Organizacao
from app.queueing import FAILED_KEY, QUEUE_KEY, cliente_redis
from app.tenancy import aplicar_contexto_tenant


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
        else:
            raise ValueError(f"Tipo de trabalho desconhecido: {tipo}")
        await session.commit()


async def main() -> None:
    redis = cliente_redis()
    proxima_manutencao = datetime.now(UTC)
    while True:
        item = await redis.blpop(QUEUE_KEY, timeout=5)
        if not item:
            if datetime.now(UTC) >= proxima_manutencao:
                for tarefa in ("assinaturas.verificar", "privacidade.verificar_retencao"):
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
            continue
        bruto = item[1]
        try:
            job = json.loads(bruto)
            await processar(job["tipo"], job.get("payload", {}))
        except Exception as exc:
            await redis.rpush(
                FAILED_KEY,
                json.dumps(
                    {"job": bruto, "erro": str(exc), "falhou_em": datetime.now(UTC).isoformat()}
                ),
            )


if __name__ == "__main__":
    asyncio.run(main())

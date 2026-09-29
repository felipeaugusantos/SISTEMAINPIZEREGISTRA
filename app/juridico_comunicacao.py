"""Comunicação durável da Operação Jurídica.

O módulo não decide, confirma ou encerra prazos. Ele transforma eventos já
registrados em comunicações rastreáveis e produz um resumo diário somente
para pessoas que já têm responsabilidade operacional explícita.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.emailing import enviar_comunicacao_juridica_rastreada
from app.marca import nome_escritorio_para_email
from app.models import Organizacao, PrazoJuridico, SaidaEmailJuridico, UsuarioOperacoes

STATUS_ATIVOS = ("aguardando_confirmacao", "pendente", "em_andamento")
FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")
MAX_TENTATIVAS = 5


def enfileirar_alerta_juridico(
    session: AsyncSession,
    *,
    organizacao_id: int,
    notificacao_id: int,
    chave: str,
    destinatario: str,
    titulo: str,
    mensagem: str,
) -> SaidaEmailJuridico:
    saida = SaidaEmailJuridico(
        organizacao_id=organizacao_id,
        notificacao_id=notificacao_id,
        chave=chave,
        tipo="alerta_prazo",
        destinatario=destinatario,
        assunto=titulo,
        mensagem=mensagem,
        status="pendente",
        tentativas=0,
    )
    session.add(saida)
    return saida


async def processar_saidas_email_juridico(
    session: AsyncSession,
    *,
    limite: int = 100,
    agora: datetime | None = None,
) -> dict[str, int]:
    """Reivindica um lote com ``SKIP LOCKED`` e registra cada resultado.

    Itens presos em ``processando`` voltam a ser elegíveis após 15 minutos,
    cobrindo encerramento abrupto do worker. O erro salvo contém somente a
    classe da exceção, nunca resposta SMTP, destinatário ou credencial.
    """
    instante = agora or datetime.now(UTC)
    presos_antes = instante - timedelta(minutes=15)
    itens = list(
        (
            await session.execute(
                select(SaidaEmailJuridico)
                .where(
                    or_(
                        (
                            (SaidaEmailJuridico.status == "pendente")
                            & (SaidaEmailJuridico.disponivel_em <= instante)
                        ),
                        (
                            (SaidaEmailJuridico.status == "processando")
                            & (SaidaEmailJuridico.processando_em < presos_antes)
                        ),
                    )
                )
                .order_by(SaidaEmailJuridico.disponivel_em, SaidaEmailJuridico.id)
                .limit(max(1, min(limite, 500)))
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for item in itens:
        item.status = "processando"
        item.processando_em = instante
        item.tentativas = int(item.tentativas or 0) + 1
    if itens:
        await session.commit()

    resultado = {"processados": 0, "enviados": 0, "reagendados": 0, "falhas": 0}
    # Fase 19.3 (white-label): remetente/assunto com o nome do escritório de
    # cada saída; uma consulta por organização no lote.
    nomes_escritorio: dict[int, str | None] = {}
    for item in itens:
        resultado["processados"] += 1
        try:
            if item.organizacao_id not in nomes_escritorio:
                nomes_escritorio[item.organizacao_id] = await nome_escritorio_para_email(session, item.organizacao_id)
            item.provedor = await enviar_comunicacao_juridica_rastreada(
                item.destinatario,
                item.assunto,
                item.mensagem,
                chave=item.chave,
                organizacao_nome=nomes_escritorio[item.organizacao_id],
            )
        except Exception as exc:  # noqa: BLE001 - isolamento por item é requisito da fila
            item.ultimo_erro = type(exc).__name__[:120]
            item.processando_em = None
            if item.tentativas >= MAX_TENTATIVAS:
                item.status = "falha"
                resultado["falhas"] += 1
            else:
                item.status = "pendente"
                item.disponivel_em = instante + timedelta(minutes=min(60, 2 ** item.tentativas))
                resultado["reagendados"] += 1
        else:
            item.status = "enviado"
            item.enviado_em = instante
            item.processando_em = None
            item.ultimo_erro = None
            resultado["enviados"] += 1
        await session.commit()
    return resultado


async def agendar_resumos_juridicos_diarios(
    session: AsyncSession,
    *,
    agora: datetime | None = None,
) -> int:
    """Cria no máximo um resumo diário por responsável e organização.

    O relatório é propositalmente operacional: contagens, sem nomes de
    clientes ou números de processo no e-mail. Detalhes permanecem atrás da
    autenticação no painel.
    """
    instante = agora or datetime.now(UTC)
    local = instante.astimezone(FUSO_BRASIL)
    if local.hour < 7:
        return 0
    inicio_hoje = datetime.combine(local.date(), time.min, FUSO_BRASIL).astimezone(UTC)
    fim_sete_dias = datetime.combine(local.date() + timedelta(days=7), time.max, FUSO_BRASIL).astimezone(UTC)
    organizacoes = list(
        (
            await session.execute(select(Organizacao.id).where(Organizacao.status != "suspensa"))
        ).scalars()
    )
    criados = 0
    for organizacao_id in organizacoes:
        linhas = (
            await session.execute(
                select(
                    UsuarioOperacoes.id,
                    UsuarioOperacoes.nome,
                    UsuarioOperacoes.email,
                    func.count(PrazoJuridico.id),
                    func.count(PrazoJuridico.id).filter(PrazoJuridico.vencimento_em < inicio_hoje),
                    func.count(PrazoJuridico.id).filter(
                        PrazoJuridico.vencimento_em >= inicio_hoje,
                        PrazoJuridico.vencimento_em <= fim_sete_dias,
                    ),
                    func.count(PrazoJuridico.id).filter(PrazoJuridico.confirmado.is_(False)),
                )
                .join(PrazoJuridico, PrazoJuridico.responsavel_id == UsuarioOperacoes.id)
                .where(
                    UsuarioOperacoes.organizacao_id == organizacao_id,
                    UsuarioOperacoes.ativo.is_(True),
                    PrazoJuridico.organizacao_id == organizacao_id,
                    PrazoJuridico.status.in_(STATUS_ATIVOS),
                )
                .group_by(UsuarioOperacoes.id, UsuarioOperacoes.nome, UsuarioOperacoes.email)
            )
        ).all()
        for usuario_id, nome, email, ativos, vencidos, proximos, confirmar in linhas:
            chave = f"resumo-juridico:{organizacao_id}:{local.date().isoformat()}:{usuario_id}"
            mensagem = (
                f"Olá, {nome}.\n\nResumo diário da sua agenda jurídica:\n"
                f"- {int(ativos or 0)} prazo(s) ativo(s)\n"
                f"- {int(vencidos or 0)} vencido(s)\n"
                f"- {int(proximos or 0)} com vencimento nos próximos 7 dias\n"
                f"- {int(confirmar or 0)} aguardando confirmação humana"
            )
            comando = (
                pg_insert(SaidaEmailJuridico)
                .values(
                    organizacao_id=organizacao_id,
                    chave=chave,
                    tipo="resumo_diario",
                    destinatario=email,
                    assunto="Resumo diário da Operação Jurídica",
                    mensagem=mensagem,
                    status="pendente",
                    tentativas=0,
                    disponivel_em=instante,
                )
                .on_conflict_do_nothing(constraint="uq_saida_email_juridico_org_chave")
            )
            retorno = await session.execute(comando)
            criados += max(0, int(retorno.rowcount or 0))
    return criados

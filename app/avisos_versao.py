"""Avisos de versão/atualização exibidos no painel + confirmação individual
de leitura (Fase 3 -- notificações e confirmação de leitura, 09/09/2026).

Confirmar leitura é sempre passivo: nunca aciona deploy, rollout nem
qualquer ação de produção -- só registra usuário, organização, data e um
IP hasheado (mesmo padrão de AssinaturaPropostaComercial).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alertas_plataforma import registrar_alerta_plataforma, resolver_alerta_plataforma
from app.models import AvisoVersao, AvisoVersaoConfirmacao, Organizacao, UsuarioOperacoes
from app.settings import get_settings


async def confirmar_leitura(
    session: AsyncSession,
    *,
    aviso_id: int,
    usuario_id: int,
    organizacao_id: int,
    ip_hash: str | None,
) -> AvisoVersaoConfirmacao:
    """Idempotente: uma segunda confirmação do mesmo usuário para o mesmo
    aviso não duplica a linha (uq_aviso_versao_confirmacao), só retorna a
    já existente."""
    existente = (
        await session.execute(
            select(AvisoVersaoConfirmacao).where(
                AvisoVersaoConfirmacao.aviso_id == aviso_id,
                AvisoVersaoConfirmacao.usuario_id == usuario_id,
            )
        )
    ).scalar_one_or_none()
    if existente is not None:
        return existente
    confirmacao = AvisoVersaoConfirmacao(
        aviso_id=aviso_id,
        organizacao_id=organizacao_id,
        usuario_id=usuario_id,
        ip_hash=ip_hash,
    )
    session.add(confirmacao)
    await session.flush()
    return confirmacao


async def lembrar_avisos_criticos_pendentes(session: AsyncSession) -> None:
    """Tarefa de manutenção "avisos.lembrar_criticos_pendentes" (Fase 3):
    para cada AvisoVersao crítico ainda ativo, publicado há mais de
    settings.aviso_critico_lembrete_horas, gera um alerta de plataforma por
    organização que ainda tem ao menos um usuário ativo sem confirmação --
    reaproveita o dedup por código e o e-mail best-effort já existentes em
    app.alertas_plataforma, em vez de inventar uma fila de e-mail nova.
    Roda com a sessão do worker em modo superadmin (app/worker.py), então
    as consultas abaixo enxergam todas as organizações sem filtro manual.
    """
    settings = get_settings()
    limite = datetime.now(UTC) - timedelta(hours=settings.aviso_critico_lembrete_horas)
    avisos = (
        (
            await session.execute(
                select(AvisoVersao).where(
                    AvisoVersao.critico.is_(True),
                    AvisoVersao.ativo.is_(True),
                    AvisoVersao.publicado_em <= limite,
                )
            )
        )
        .scalars()
        .all()
    )
    for aviso in avisos:
        organizacoes_alvo = select(Organizacao.id)
        if aviso.organizacao_id is not None:
            organizacoes_alvo = organizacoes_alvo.where(Organizacao.id == aviso.organizacao_id)
        ids_organizacoes = (await session.execute(organizacoes_alvo)).scalars().all()
        for organizacao_id in ids_organizacoes:
            pendentes = (
                await session.execute(
                    select(func.count())
                    .select_from(UsuarioOperacoes)
                    .outerjoin(
                        AvisoVersaoConfirmacao,
                        (AvisoVersaoConfirmacao.usuario_id == UsuarioOperacoes.id)
                        & (AvisoVersaoConfirmacao.aviso_id == aviso.id),
                    )
                    .where(
                        UsuarioOperacoes.organizacao_id == organizacao_id,
                        UsuarioOperacoes.ativo.is_(True),
                        AvisoVersaoConfirmacao.id.is_(None),
                    )
                )
            ).scalar_one()
            codigo = f"AVISO_CRITICO_PENDENTE_{aviso.id}"
            if pendentes > 0:
                await registrar_alerta_plataforma(
                    session,
                    codigo=codigo,
                    severidade="aviso",
                    organizacao_id=organizacao_id,
                    mensagem=(
                        f'{pendentes} usuário(s) ainda não confirmaram a leitura do aviso crítico '
                        f'"{aviso.titulo}" (versão {aviso.versao}, publicado em {aviso.publicado_em:%d/%m/%Y}).'
                    ),
                    detalhes={"aviso_id": aviso.id, "pendentes": pendentes},
                )
            else:
                await resolver_alerta_plataforma(session, codigo=codigo, organizacao_id=organizacao_id)

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TokenRecuperacaoSenha


async def invalidar_links_recuperacao(session: AsyncSession, usuario_id: int) -> None:
    """Chamado sob a trava da conta, na mesma transação que altera sua senha."""
    await session.execute(
        update(TokenRecuperacaoSenha)
        .where(TokenRecuperacaoSenha.usuario_id == usuario_id, TokenRecuperacaoSenha.usado_em.is_(None))
        .values(usado_em=datetime.now(UTC))
    )

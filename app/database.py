from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.settings import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _aplicar_rls_ao_iniciar_transacao(session: Session, _transaction, connection) -> None:
    """Reaplica o tenant em toda transacao, inclusive depois de commit/rollback."""
    organizacao_id = session.info.get("organizacao_id")
    if organizacao_id is None:
        return
    connection.execute(
        text(
            "SELECT set_config('app.organizacao_id', :organizacao_id, true), "
            "set_config('app.superadmin', :superadmin, true)"
        ),
        {
            "organizacao_id": str(organizacao_id),
            "superadmin": "true" if session.info.get("superadmin") else "false",
        },
    )


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session

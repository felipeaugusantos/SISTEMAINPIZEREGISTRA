from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.settings import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)

ESCOPOS_AUTENTICACAO = frozenset(
    {
        "convite",
        "dominio",
        "integracao",
        "login",
        "oauth_criar",
        "oauth_email",
        "oauth_identidade",
        "oauth_mfa",
        "oauth_state",
        "recuperacao_email",
        "recuperacao_token",
        "sessao",
    }
)


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


async def aplicar_contexto_autenticacao(
    session: AsyncSession,
    escopo: str,
    valor: str = "",
    auxiliar: str = "",
) -> None:
    """Autoriza somente uma busca pre-tenant exata durante a transacao atual."""
    if escopo not in ESCOPOS_AUTENTICACAO:
        raise ValueError(f"Escopo de autenticacao desconhecido: {escopo}")
    if not hasattr(session, "info"):
        return
    await session.execute(
        text(
            "SELECT set_config('app.auth_scope', :escopo, true), "
            "set_config('app.auth_value', :valor, true), "
            "set_config('app.auth_aux', :auxiliar, true)"
        ),
        {"escopo": escopo, "valor": valor, "auxiliar": auxiliar},
    )

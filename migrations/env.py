from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.database import Base
from app.models import (  # noqa: F401
    ClassificacaoMarca,
    Lead,
    Movimentacao,
    PesquisaMarca,
    Processo,
    Titular,
)
from app.settings import get_settings

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

INDEXES_SQL_MANUAL = {
    "ix_processos_titulo_trgm",
    "ix_titulares_nome_trgm",
    "uq_leads_org_email_ativos",
}


def incluir_objeto(objeto, nome, tipo, refletido, comparar_com) -> bool:
    """Preserva Ã­ndices PostgreSQL por expressÃ£o criados em SQL nas migraÃ§Ãµes."""
    if tipo == "index" and refletido and comparar_com is None and nome in INDEXES_SQL_MANUAL:
        return False
    if tipo == "unique_constraint" and refletido and nome == "uq_titulares_nome_pais":
        return False
    return True


def comparar_tipo(_contexto, _coluna_inspecionada, _coluna_modelo, tipo_inspecionado, tipo_modelo):
    # Enums legados foram persistidos como VARCHAR com constraints CHECK.
    if getattr(tipo_modelo, "enum_class", None) is not None and tipo_inspecionado.__class__.__name__ == "VARCHAR":
        return False
    return None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=comparar_tipo,
        include_object=incluir_objeto,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=comparar_tipo,
        include_object=incluir_objeto,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    import asyncio

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

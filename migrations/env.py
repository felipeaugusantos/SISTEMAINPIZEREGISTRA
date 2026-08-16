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
    PropostaComercial,
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
    "ix_processos_procurador_unaccent_trgm",
    "ix_titulares_nome_trgm",
    "uq_exclusao_pesquisa_pendente",
    "uq_leads_org_email_ativos",
}

# Constraints compostas de tenant da Fase 7 são deliberadamente mantidas no
# banco, em paralelo às FKs simples usadas pelos relacionamentos do ORM. Declará-las
# novamente no metadata tornaria esses relacionamentos ambíguos para o SQLAlchemy.
CONSTRAINTS_SQL_MANUAL = {
    "fk_checklist_prazo_tenant",
    "fk_contato_empresa_tenant",
    "fk_evento_juridico_monitorado_tenant",
    "fk_evento_juridico_prazo_tenant",
    "fk_historico_lancamento_tenant",
    "fk_historico_parcela_tenant",
    "fk_interacao_empresa_tenant",
    "fk_interacao_lead_tenant",
    "fk_lancamento_categoria_tenant",
    "fk_lancamento_empresa_tenant",
    "fk_lancamento_forma_tenant",
    "fk_lead_contato_empresa_tenant",
    "fk_lead_empresa_tenant",
    "fk_lembrete_lead_tenant",
    "fk_monitorado_empresa_tenant",
    "fk_notificacao_prazo_tenant",
    "fk_parcela_forma_tenant",
    "fk_parcela_lancamento_tenant",
    "fk_pesquisa_empresa_tenant",
    "fk_pesquisa_lead_tenant",
    "fk_prazo_monitorado_tenant",
    "uq_categoria_fin_org_id",
    "uq_contato_org_empresa_id",
    "uq_empresa_crm_org_id",
    "uq_forma_fin_org_id",
    "uq_lancamento_fin_org_id",
    "uq_lead_org_id",
    "uq_lembrete_crm_idempotencia",
    "uq_monitorado_org_id",
    "uq_parcela_fin_org_id",
    "uq_pesquisa_org_id",
    "uq_prazo_org_id",
    "uq_usuario_org_id",
}


def incluir_objeto(objeto, nome, tipo, refletido, comparar_com) -> bool:
    """Preserva Ã­ndices PostgreSQL por expressÃ£o criados em SQL nas migraÃ§Ãµes."""
    if tipo == "index" and refletido and comparar_com is None and nome in INDEXES_SQL_MANUAL:
        return False
    if tipo == "unique_constraint" and refletido and nome == "uq_titulares_nome_pais":
        return False
    if (
        tipo in {"foreign_key_constraint", "unique_constraint"}
        and refletido
        and comparar_com is None
        and nome in CONSTRAINTS_SQL_MANUAL
    ):
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

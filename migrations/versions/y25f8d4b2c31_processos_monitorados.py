"""carteira tenant de processos monitorados

Revision ID: y25f8d4b2c31
Revises: x24e7c3a9b11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "y25f8d4b2c31"
down_revision: str | None = "x24e7c3a9b11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "processos_monitorados",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("processo_id", sa.BigInteger(), nullable=False),
        sa.Column("empresa_id", sa.BigInteger(), nullable=True),
        sa.Column("responsavel_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="ativo", nullable=False),
        sa.Column("origem", sa.String(length=30), server_default="manual", nullable=False),
        sa.Column("procurador_origem", sa.Text(), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("vinculado_por", sa.String(length=254), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas_crm.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["processo_id"], ["processos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["responsavel_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organizacao_id", "processo_id", name="uq_processo_monitorado_org_processo"
        ),
    )
    for coluna in (
        "organizacao_id",
        "processo_id",
        "empresa_id",
        "responsavel_id",
        "status",
        "origem",
        "criado_em",
    ):
        op.create_index(
            f"ix_processos_monitorados_{coluna}", "processos_monitorados", [coluna]
        )
    op.execute('ALTER TABLE "processos_monitorados" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "processos_monitorados" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "processos_monitorados" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON "processos_monitorados" TO inpi_app'
    )
    op.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app')
    op.execute(
        r"CREATE INDEX IF NOT EXISTS ix_processos_procurador_unaccent_trgm "
        r"ON processos USING gin ((regexp_replace(trim(immutable_unaccent(lower(procurador))), "
        r"'\s+', ' ', 'g')) gin_trgm_ops) WHERE procurador IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_processos_procurador_unaccent_trgm")
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "processos_monitorados"')
    op.drop_table("processos_monitorados")

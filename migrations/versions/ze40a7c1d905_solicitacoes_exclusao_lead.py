"""pedidos de exclusao de lead com aprovacao administrativa

Revision ID: ze40a7c1d905
Revises: zb22d4e5f607
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ze40a7c1d905"
down_revision: str | None = "zb22d4e5f607"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"
TABELA = "solicitacoes_exclusao_lead"


def upgrade() -> None:
    op.create_table(
        TABELA,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_nome", sa.String(length=200), nullable=False),
        sa.Column("solicitado_por_id", sa.BigInteger(), nullable=True),
        sa.Column("solicitado_por", sa.String(length=254), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pendente", nullable=False),
        sa.Column("decidido_por_id", sa.BigInteger(), nullable=True),
        sa.Column("decidido_por", sa.String(length=254), nullable=True),
        sa.Column("decisao_observacao", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["solicitado_por_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decidido_por_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("organizacao_id", "lead_id", "solicitado_por_id", "decidido_por_id", "status", "criado_em"):
        op.create_index(f"ix_{TABELA}_{coluna}", TABELA, [coluna])
    op.create_index(
        "uq_exclusao_lead_pendente",
        TABELA,
        ["organizacao_id", "lead_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pendente'"),
    )
    op.execute(f'ALTER TABLE "{TABELA}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABELA}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{TABELA}" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{TABELA}" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{TABELA}"')
    op.drop_table(TABELA)

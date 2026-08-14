"""guias INPI (GRU) por lead — registro e controle

Revision ID: zo11j6n2h408
Revises: zn00i5m1g397
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zo11j6n2h408"
down_revision: str | None = "zn00i5m1g397"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "guias_inpi",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("servico", sa.String(60), nullable=True),
        sa.Column("codigo", sa.String(10), nullable=True),
        sa.Column("descricao", sa.String(200), nullable=False),
        sa.Column("valor", sa.Numeric(12, 2), nullable=True),
        sa.Column("reduzido", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("numero_gru", sa.String(60), nullable=True),
        sa.Column("vencimento", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pendente", nullable=False),
        sa.Column("pago_em", sa.Date(), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pendente','paga','cancelada')", name="ck_guias_inpi_status"
        ),
    )
    op.create_index("ix_guias_inpi_organizacao_id", "guias_inpi", ["organizacao_id"])
    op.create_index("ix_guias_inpi_lead_id", "guias_inpi", ["lead_id"])
    op.create_index("ix_guias_inpi_vencimento", "guias_inpi", ["vencimento"])
    op.create_index("ix_guias_inpi_status", "guias_inpi", ["status"])

    op.execute('ALTER TABLE "guias_inpi" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "guias_inpi" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "guias_inpi" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "guias_inpi" TO inpi_app')


def downgrade() -> None:
    op.drop_table("guias_inpi")

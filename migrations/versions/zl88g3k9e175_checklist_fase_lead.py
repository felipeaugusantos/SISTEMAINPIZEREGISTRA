"""checklist por etapa (fase) do funil do lead

Revision ID: zl88g3k9e175
Revises: zk77f2j8d064
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zl88g3k9e175"
down_revision: str | None = "zk77f2j8d064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "checklist_fase_lead",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("fase", sa.String(30), nullable=False),
        sa.Column("descricao", sa.String(300), nullable=False),
        sa.Column("concluido", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checklist_fase_lead_organizacao_id", "checklist_fase_lead", ["organizacao_id"]
    )
    op.create_index("ix_checklist_fase_lead_lead_id", "checklist_fase_lead", ["lead_id"])
    op.create_index("ix_checklist_fase_lead_fase", "checklist_fase_lead", ["fase"])

    op.execute('ALTER TABLE "checklist_fase_lead" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "checklist_fase_lead" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "checklist_fase_lead" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "checklist_fase_lead" TO inpi_app')


def downgrade() -> None:
    op.drop_table("checklist_fase_lead")

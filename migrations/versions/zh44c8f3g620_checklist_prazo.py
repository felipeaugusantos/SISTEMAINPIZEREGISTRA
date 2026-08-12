"""checklist operacional por prazo jurídico

Revision ID: zh44c8f3g620
Revises: zg33b7e2f519
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zh44c8f3g620"
down_revision: str | None = "zg33b7e2f519"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "itens_checklist_prazo",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("prazo_id", sa.BigInteger(), nullable=False),
        sa.Column("descricao", sa.String(300), nullable=False),
        sa.Column("concluido", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluido_por", sa.String(254), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prazo_id"], ["prazos_juridicos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_itens_checklist_prazo_organizacao_id", "itens_checklist_prazo", ["organizacao_id"]
    )
    op.create_index("ix_itens_checklist_prazo_prazo_id", "itens_checklist_prazo", ["prazo_id"])
    op.create_index("ix_itens_checklist_prazo_concluido", "itens_checklist_prazo", ["concluido"])

    op.execute('ALTER TABLE "itens_checklist_prazo" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "itens_checklist_prazo" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "itens_checklist_prazo" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "itens_checklist_prazo" TO inpi_app')


def downgrade() -> None:
    op.drop_table("itens_checklist_prazo")

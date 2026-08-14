"""regras de automação (override por org de ativo/dias)

Revision ID: zv88q3u9o175
Revises: zu77p2t8n064
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zv88q3u9o175"
down_revision: str | None = "zu77p2t8n064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "regras_automacao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("chave", sa.String(40), nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("dias", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "chave", name="uq_regra_automacao_org_chave"),
    )
    op.create_index("ix_regras_automacao_organizacao_id", "regras_automacao", ["organizacao_id"])
    op.create_index("ix_regras_automacao_chave", "regras_automacao", ["chave"])
    op.execute('ALTER TABLE "regras_automacao" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "regras_automacao" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "regras_automacao" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "regras_automacao" TO inpi_app')


def downgrade() -> None:
    op.drop_table("regras_automacao")

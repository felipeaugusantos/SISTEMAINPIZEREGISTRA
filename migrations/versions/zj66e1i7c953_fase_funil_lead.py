"""fase do lead no funil de atendimento + histórico de transições

Adiciona a coluna 'fase' aos leads (etapa do funil, de contato_inicial a
processo_inpi) e a tabela historico_fase_lead com o registro de cada transição.

Revision ID: zj66e1i7c953
Revises: zi55d0h6b842
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zj66e1i7c953"
down_revision: str | None = "zi55d0h6b842"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "fase",
            sa.String(30),
            server_default="contato_inicial",
            nullable=False,
        ),
    )
    op.create_index("ix_leads_fase", "leads", ["fase"])

    op.create_table(
        "historico_fase_lead",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("fase", sa.String(30), nullable=False),
        sa.Column(
            "entrou_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("por", sa.String(150), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_historico_fase_lead_organizacao_id", "historico_fase_lead", ["organizacao_id"]
    )
    op.create_index("ix_historico_fase_lead_lead_id", "historico_fase_lead", ["lead_id"])
    op.create_index("ix_historico_fase_lead_entrou_em", "historico_fase_lead", ["entrou_em"])

    op.execute('ALTER TABLE "historico_fase_lead" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "historico_fase_lead" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "historico_fase_lead" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "historico_fase_lead" TO inpi_app')


def downgrade() -> None:
    op.drop_table("historico_fase_lead")
    op.drop_index("ix_leads_fase", table_name="leads")
    op.drop_column("leads", "fase")

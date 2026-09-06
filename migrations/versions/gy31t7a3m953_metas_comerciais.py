"""metas comerciais: meta mensal de leads ganhos e valor faturado por operador

Revision ID: gy31t7a3m953
Revises: fx20s6z2l942

Achado item 50 da auditoria completa do CRM (06/09/2026): não existia
nenhum modelo/endpoint de meta comercial -- decisão do usuário: meta
mensal por operador medindo quantidade de leads ganhos E valor faturado.
Não há registro separado de "meta de equipe" -- a meta de equipe é a soma
das metas individuais do período, calculada na consulta (mesma decisão já
tomada para pipeline previsto/forecast: não persistir o que pode ser
derivado, para não criar uma segunda fonte de verdade).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "gy31t7a3m953"
down_revision: str | None = "fx20s6z2l942"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "metas_comerciais",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("operador_id", sa.BigInteger(), nullable=False),
        sa.Column("periodo", sa.String(length=7), nullable=False),
        sa.Column("meta_leads_ganhos", sa.Integer(), server_default="0", nullable=False),
        sa.Column("meta_valor_faturado", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("definida_por", sa.String(length=254), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["operador_id"], ["usuarios_operacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organizacao_id", "operador_id", "periodo", name="uq_meta_comercial_operador_periodo"
        ),
        sa.CheckConstraint("meta_leads_ganhos >= 0", name="ck_meta_comercial_leads_ganhos"),
        sa.CheckConstraint("meta_valor_faturado >= 0", name="ck_meta_comercial_valor_faturado"),
    )
    op.create_index("ix_metas_comerciais_organizacao_id", "metas_comerciais", ["organizacao_id"])
    op.create_index("ix_metas_comerciais_operador_id", "metas_comerciais", ["operador_id"])
    op.create_index("ix_metas_comerciais_periodo", "metas_comerciais", ["periodo"])
    op.execute('ALTER TABLE "metas_comerciais" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "metas_comerciais" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "metas_comerciais" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "metas_comerciais" TO inpi_app')


def downgrade() -> None:
    op.drop_table("metas_comerciais")

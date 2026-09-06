"""horas faturaveis: apontamento manual por lead/processo (FASE-A-CRM-1)

Revision ID: zw08g6y0j519
Revises: zu97f5x9i408

Achado FASE-A da auditoria do CRM (05/09/2026): nao existia nenhum
controle de horas trabalhadas, so custo monetario (CustoJuridico). Este
apontamento e vinculado a um lead e/ou a um processo monitorado.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zw08g6y0j519"
down_revision: str | None = "zu97f5x9i408"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "apontamentos_horas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=True),
        sa.Column("processo_monitorado_id", sa.BigInteger(), nullable=True),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("horas", sa.Numeric(5, 2), nullable=False),
        sa.Column("descricao", sa.String(length=500), nullable=False),
        sa.Column("faturavel", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios_operacoes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["processo_monitorado_id"], ["processos_monitorados.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "processo_monitorado_id IS NOT NULL OR lead_id IS NOT NULL",
            name="ck_apontamento_horas_vinculo",
        ),
        sa.CheckConstraint("horas > 0", name="ck_apontamento_horas_positivas"),
    )
    op.create_index("ix_apontamentos_horas_organizacao_id", "apontamentos_horas", ["organizacao_id"])
    op.create_index("ix_apontamentos_horas_usuario_id", "apontamentos_horas", ["usuario_id"])
    op.create_index("ix_apontamentos_horas_lead_id", "apontamentos_horas", ["lead_id"])
    op.create_index(
        "ix_apontamentos_horas_processo_monitorado_id", "apontamentos_horas", ["processo_monitorado_id"]
    )
    op.create_index("ix_apontamentos_horas_data", "apontamentos_horas", ["data"])
    op.execute('ALTER TABLE "apontamentos_horas" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "apontamentos_horas" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "apontamentos_horas" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "apontamentos_horas" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_index("ix_apontamentos_horas_data", table_name="apontamentos_horas")
    op.drop_index("ix_apontamentos_horas_processo_monitorado_id", table_name="apontamentos_horas")
    op.drop_index("ix_apontamentos_horas_lead_id", table_name="apontamentos_horas")
    op.drop_index("ix_apontamentos_horas_usuario_id", table_name="apontamentos_horas")
    op.drop_index("ix_apontamentos_horas_organizacao_id", table_name="apontamentos_horas")
    op.drop_table("apontamentos_horas")

"""kanban auditavel dos processos monitorados

Revision ID: zq33l8p4j620
Revises: zp22k7o3i519
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zq33l8p4j620"
down_revision: str | None = "zp22k7o3i519"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.add_column(
        "processos_monitorados",
        sa.Column("etapa_kanban", sa.String(30), server_default="triagem", nullable=False),
    )
    op.add_column(
        "processos_monitorados",
        sa.Column("ordem_kanban", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "processos_monitorados",
        sa.Column(
            "etapa_atualizada_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "processos_monitorados",
        sa.Column("etapa_atualizada_por", sa.String(254), nullable=True),
    )
    op.create_check_constraint(
        "ck_processo_monitorado_etapa_kanban",
        "processos_monitorados",
        "etapa_kanban IN ('triagem','aguardando_documentos','documentacao_gru',"
        "'protocolado','aguardando_inpi','exigencia_recurso','deferido_concessao',"
        "'encerrado')",
    )
    op.create_index(
        "ix_processos_monitorados_etapa_kanban",
        "processos_monitorados",
        ["etapa_kanban"],
    )
    op.create_index(
        "ix_processos_monitorados_etapa_atualizada_em",
        "processos_monitorados",
        ["etapa_atualizada_em"],
    )

    op.create_table(
        "historico_etapas_carteira",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("processo_monitorado_id", sa.BigInteger(), nullable=False),
        sa.Column("etapa_anterior", sa.String(30), nullable=True),
        sa.Column("etapa_nova", sa.String(30), nullable=False),
        sa.Column("movido_por", sa.String(254), nullable=False),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["processo_monitorado_id"], ["processos_monitorados.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("organizacao_id", "processo_monitorado_id", "etapa_nova", "criado_em"):
        op.create_index(
            f"ix_historico_etapas_carteira_{coluna}",
            "historico_etapas_carteira",
            [coluna],
        )
    op.execute('ALTER TABLE "historico_etapas_carteira" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "historico_etapas_carteira" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "historico_etapas_carteira" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON "historico_etapas_carteira" TO inpi_app'
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("historico_etapas_carteira")
    op.drop_index(
        "ix_processos_monitorados_etapa_atualizada_em",
        table_name="processos_monitorados",
    )
    op.drop_index(
        "ix_processos_monitorados_etapa_kanban", table_name="processos_monitorados"
    )
    op.drop_constraint(
        "ck_processo_monitorado_etapa_kanban",
        "processos_monitorados",
        type_="check",
    )
    op.drop_column("processos_monitorados", "etapa_atualizada_por")
    op.drop_column("processos_monitorados", "etapa_atualizada_em")
    op.drop_column("processos_monitorados", "ordem_kanban")
    op.drop_column("processos_monitorados", "etapa_kanban")

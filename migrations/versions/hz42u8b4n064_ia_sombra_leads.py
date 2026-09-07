"""ia em sombra: sugestoes de resumo + proxima acao por lead, opt-in por organizacao

Revision ID: hz42u8b4n064
Revises: ha53v9c5o175

Nova frente fora do roteiro da auditoria completa do CRM: uma IA que roda
em segundo plano, gera resumo do historico + sugestao de proxima acao por
lead, mas nunca decide nem envia nada sozinha -- sempre exige revisao
humana explicita. Modelo local (Ollama, sem chave de API, sem custo por
chamada, nenhum dado de lead sai do servidor), desligado por padrao em
dois niveis: settings.ia_sombra_enabled (kill-switch global) e
PoliticaCRM.ia_sombra_ativa (opt-in por organizacao).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "hz42u8b4n064"
down_revision: str | None = "ha53v9c5o175"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.add_column(
        "politicas_crm",
        sa.Column("ia_sombra_ativa", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "sugestoes_ia_lead",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("gerado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("modelo", sa.String(length=120), nullable=False),
        sa.Column("resumo", sa.Text(), server_default="", nullable=False),
        sa.Column("sugestao_proxima_acao", sa.Text(), server_default="", nullable=False),
        sa.Column("baseado_em_evento_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pendente", nullable=False),
        sa.Column("revisado_por", sa.String(length=254), nullable=True),
        sa.Column("revisado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sugestoes_ia_lead_organizacao_id", "sugestoes_ia_lead", ["organizacao_id"])
    op.create_index("ix_sugestoes_ia_lead_lead_id", "sugestoes_ia_lead", ["lead_id"])
    op.create_index("ix_sugestoes_ia_lead_gerado_em", "sugestoes_ia_lead", ["gerado_em"])
    op.create_index("ix_sugestoes_ia_lead_status", "sugestoes_ia_lead", ["status"])
    op.execute('ALTER TABLE "sugestoes_ia_lead" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "sugestoes_ia_lead" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "sugestoes_ia_lead" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "sugestoes_ia_lead" TO inpi_app')


def downgrade() -> None:
    op.drop_table("sugestoes_ia_lead")
    op.drop_column("politicas_crm", "ia_sombra_ativa")

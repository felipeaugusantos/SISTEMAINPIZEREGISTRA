"""radar de prospeccao: estrutura de Prospect (Fase 1)

Revision ID: bsllcopj8crg
Revises: z7ckyrsnnqgj

Fase 1 do roadmap do Radar de Prospeccao (03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md): tabelas base de Prospect e
sua timeline de status. Colunas de fonte/campanha (Fase 2), presenca digital
(Fase 3), triagem de marca (Fase 4) e score (Fase 5) entram por migracao
propria em cada fase.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bsllcopj8crg"
down_revision: str | None = "z7ckyrsnnqgj"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"

STATUS_PROSPECT = ("novo", "rejeitado", "duplicado", "convertido_lead")


def upgrade() -> None:
    op.create_table(
        "prospects",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("cnpj", sa.String(length=18), nullable=True),
        sa.Column("razao_social", sa.String(length=200), nullable=False),
        sa.Column("nome_fantasia", sa.String(length=200), nullable=True),
        sa.Column("cnae_principal", sa.String(length=10), nullable=True),
        sa.Column("cnaes_secundarios", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("porte", sa.String(length=20), nullable=True),
        sa.Column("situacao_cadastral", sa.String(length=20), nullable=True),
        sa.Column("data_abertura", sa.Date(), nullable=True),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("cidade", sa.String(length=120), nullable=True),
        sa.Column("endereco", sa.JSON(), nullable=True),
        sa.Column("telefone", sa.String(length=30), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("site", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="novo"),
        sa.Column("motivo_descarte", sa.String(length=30), nullable=True),
        sa.Column("responsavel_id", sa.BigInteger(), nullable=True),
        sa.Column("lead_id", sa.BigInteger(), nullable=True),
        sa.Column("empresa_crm_id", sa.BigInteger(), nullable=True),
        sa.Column("duplicado_de_id", sa.BigInteger(), nullable=True),
        sa.Column("dados_brutos", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["responsavel_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["empresa_crm_id"], ["empresas_crm.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["duplicado_de_id"], ["prospects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", name="uq_prospects_lead_id"),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{valor}'" for valor in STATUS_PROSPECT) + ")",
            name="ck_prospects_status_valido",
        ),
    )
    op.create_index("ix_prospects_organizacao_id", "prospects", ["organizacao_id"])
    op.create_index("ix_prospects_cnpj", "prospects", ["cnpj"])
    op.create_index("ix_prospects_razao_social", "prospects", ["razao_social"])
    op.create_index("ix_prospects_cnae_principal", "prospects", ["cnae_principal"])
    op.create_index("ix_prospects_uf", "prospects", ["uf"])
    op.create_index("ix_prospects_cidade", "prospects", ["cidade"])
    op.create_index("ix_prospects_telefone", "prospects", ["telefone"])
    op.create_index("ix_prospects_email", "prospects", ["email"])
    op.create_index("ix_prospects_status", "prospects", ["status"])
    op.create_index("ix_prospects_responsavel_id", "prospects", ["responsavel_id"])
    op.create_index("ix_prospects_empresa_crm_id", "prospects", ["empresa_crm_id"])
    op.create_index("ix_prospects_duplicado_de_id", "prospects", ["duplicado_de_id"])
    op.create_index("ix_prospects_org_status", "prospects", ["organizacao_id", "status"])
    op.create_index("ix_prospects_org_uf_cidade", "prospects", ["organizacao_id", "uf", "cidade"])
    op.execute('ALTER TABLE "prospects" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "prospects" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "prospects" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "prospects" TO inpi_app')

    op.create_table(
        "historico_status_prospect",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("prospect_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("entrou_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("por", sa.String(length=150), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prospect_id"], ["prospects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_historico_status_prospect_organizacao_id", "historico_status_prospect", ["organizacao_id"])
    op.create_index("ix_historico_status_prospect_prospect_id", "historico_status_prospect", ["prospect_id"])
    op.create_index("ix_historico_status_prospect_entrou_em", "historico_status_prospect", ["entrou_em"])
    op.execute('ALTER TABLE "historico_status_prospect" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "historico_status_prospect" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "historico_status_prospect" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "historico_status_prospect" TO inpi_app')


def downgrade() -> None:
    op.drop_table("historico_status_prospect")
    op.drop_table("prospects")

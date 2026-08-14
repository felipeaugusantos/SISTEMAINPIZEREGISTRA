"""separação empresa/contato: enriquece empresa, cria contatos, liga lead->contato

Revision ID: zu77p2t8n064
Revises: zt66o1s7m953
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zu77p2t8n064"
down_revision: str | None = "zt66o1s7m953"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    # 1) enriquecer empresa
    op.add_column("empresas_crm", sa.Column("documento", sa.String(18), nullable=True))
    op.add_column("empresas_crm", sa.Column("segmento", sa.String(80), nullable=True))
    op.add_column("empresas_crm", sa.Column("telefone", sa.String(30), nullable=True))
    op.add_column("empresas_crm", sa.Column("email", sa.String(254), nullable=True))
    op.add_column("empresas_crm", sa.Column("site", sa.String(200), nullable=True))
    op.add_column("empresas_crm", sa.Column("observacoes", sa.Text(), nullable=True))
    op.create_index("ix_empresas_crm_documento", "empresas_crm", ["documento"])

    # 2) tabela de contatos (pessoas)
    op.create_table(
        "contatos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("empresa_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(150), nullable=False),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("telefone", sa.String(30), nullable=True),
        sa.Column("cargo", sa.String(80), nullable=True),
        sa.Column("principal", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas_crm.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contatos_organizacao_id", "contatos", ["organizacao_id"])
    op.create_index("ix_contatos_empresa_id", "contatos", ["empresa_id"])
    op.create_index("ix_contatos_nome", "contatos", ["nome"])
    op.create_index("ix_contatos_email", "contatos", ["email"])
    op.execute('ALTER TABLE "contatos" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "contatos" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "contatos" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "contatos" TO inpi_app')

    # 3) lead -> contato
    op.add_column("leads", sa.Column("contato_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_leads_contato_id", "leads", ["contato_id"])
    op.create_foreign_key(
        "fk_leads_contato_id", "leads", "contatos", ["contato_id"], ["id"], ondelete="SET NULL"
    )

    # 4) backfill: um contato por (empresa, e-mail) a partir dos leads, e liga o lead
    op.execute(
        "INSERT INTO contatos (organizacao_id, empresa_id, nome, email, telefone, principal) "
        "SELECT DISTINCT ON (l.empresa_id, lower(l.email)) "
        "  l.organizacao_id, l.empresa_id, l.nome, NULLIF(l.email,''), NULLIF(l.telefone,''), true "
        "FROM leads l "
        "WHERE l.empresa_id IS NOT NULL AND l.arquivado_em IS NULL AND l.email IS NOT NULL AND l.email <> '' "
        "ORDER BY l.empresa_id, lower(l.email), l.criado_em"
    )
    op.execute(
        "UPDATE leads l SET contato_id = c.id "
        "FROM contatos c "
        "WHERE l.contato_id IS NULL AND l.empresa_id = c.empresa_id "
        "AND lower(l.email) = lower(c.email)"
    )


def downgrade() -> None:
    op.drop_constraint("fk_leads_contato_id", "leads", type_="foreignkey")
    op.drop_index("ix_leads_contato_id", table_name="leads")
    op.drop_column("leads", "contato_id")
    op.drop_table("contatos")
    op.drop_index("ix_empresas_crm_documento", table_name="empresas_crm")
    for col in ("observacoes", "site", "email", "telefone", "segmento", "documento"):
        op.drop_column("empresas_crm", col)

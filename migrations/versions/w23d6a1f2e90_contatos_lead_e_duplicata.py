"""histórico de contatos por lead (contatos_lead) e flag de pesquisa duplicada

Revision ID: w23d6a1f2e90
Revises: v22c5f0e1d80
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "w23d6a1f2e90"
down_revision: str | None = "v22c5f0e1d80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constantes fixas (não são entrada de usuário) — mesmo padrão das migrações RLS do repo.
TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "contatos_lead",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "lead_id",
            sa.BigInteger(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "pesquisa_id",
            sa.String(length=36),
            sa.ForeignKey("pesquisas_marca.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "operador_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("operador_nome", sa.String(length=150), nullable=True),
        sa.Column("canal", sa.String(length=20), nullable=False, server_default="telefone"),
        sa.Column("resultado", sa.String(length=150), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.CheckConstraint(
            "canal IN ('telefone','email','whatsapp','reuniao','outro')",
            name="canal_contato",
        ),
    )
    op.execute('ALTER TABLE "contatos_lead" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "contatos_lead" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "contatos_lead" '
        f'USING ({SUPER} OR organizacao_id = {TENANT}) '
        f'WITH CHECK ({SUPER} OR organizacao_id = {TENANT})'
    )

    op.add_column(
        "pesquisas_marca",
        sa.Column("duplicada", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "pesquisas_marca",
        sa.Column("pesquisa_original_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_pesquisas_marca_duplicada", "pesquisas_marca", ["duplicada"])
    op.create_index(
        "ix_pesquisas_marca_pesquisa_original_id", "pesquisas_marca", ["pesquisa_original_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_pesquisas_marca_pesquisa_original_id", table_name="pesquisas_marca")
    op.drop_index("ix_pesquisas_marca_duplicada", table_name="pesquisas_marca")
    op.drop_column("pesquisas_marca", "pesquisa_original_id")
    op.drop_column("pesquisas_marca", "duplicada")
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "contatos_lead"')
    op.drop_table("contatos_lead")

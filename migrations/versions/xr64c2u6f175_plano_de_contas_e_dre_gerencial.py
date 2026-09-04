"""plano de contas gerencial e classificacao contabil de lancamentos (FASE7-13/14)

Revision ID: xr64c2u6f175
Revises: wp43q1s5d064

Achado FASE7-13/14 da auditoria (04/09/2026): o financeiro tinha dimensoes
analiticas soltas (CategoriaFinanceira, CentroCustoFinanceiro) mas nenhum
plano de contas contabil nem DRE gerencial. Ver app/plano_contas.py para o
seed padrao e a logica de agregacao. conta_contabil_id em
lancamentos_financeiros e opcional -- lancamentos existentes continuam
funcionando sem classificacao.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "xr64c2u6f175"
down_revision: str | None = "wp43q1s5d064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "plano_contas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("conta_pai_id", sa.BigInteger(), nullable=True),
        sa.Column("codigo", sa.String(length=20), nullable=False),
        sa.Column("nome", sa.String(length=150), nullable=False),
        sa.Column("natureza", sa.String(length=10), nullable=False),
        sa.Column("grupo_dre", sa.String(length=30), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conta_pai_id"], ["plano_contas.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "codigo", name="uq_plano_contas_codigo"),
    )
    op.create_index("ix_plano_contas_organizacao_id", "plano_contas", ["organizacao_id"])
    op.create_index("ix_plano_contas_conta_pai_id", "plano_contas", ["conta_pai_id"])
    op.create_index("ix_plano_contas_codigo", "plano_contas", ["codigo"])
    op.create_index("ix_plano_contas_grupo_dre", "plano_contas", ["grupo_dre"])
    op.create_index("ix_plano_contas_ativo", "plano_contas", ["ativo"])

    op.execute('ALTER TABLE "plano_contas" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "plano_contas" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "plano_contas" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "plano_contas" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")

    op.add_column("lancamentos_financeiros", sa.Column("conta_contabil_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_lancamentos_financeiros_conta_contabil_id", "lancamentos_financeiros", ["conta_contabil_id"]
    )
    op.create_foreign_key(
        "fk_lancamento_conta_contabil",
        "lancamentos_financeiros",
        "plano_contas",
        ["conta_contabil_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_lancamento_conta_contabil", "lancamentos_financeiros", type_="foreignkey")
    op.drop_index("ix_lancamentos_financeiros_conta_contabil_id", table_name="lancamentos_financeiros")
    op.drop_column("lancamentos_financeiros", "conta_contabil_id")
    op.drop_table("plano_contas")

"""materiais de identidade visual do cliente no portal

Revision ID: a1b2c3d4e5f6
Revises: f0954bcdec31

Item 2 do pedido de melhorias do cliente final (17/09/2026): area de
Identidade Visual por cliente. Escopo definido com o usuario: somente a
equipe interna cadastra materiais (logo, manual de marca, artes prontas);
o cliente so visualiza e baixa no portal.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f0954bcdec31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "materiais_marca_clientes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("descricao", sa.String(300), nullable=True),
        sa.Column("caminho", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("tamanho", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("arquivo_hash", sa.String(64), nullable=True),
        sa.Column("enviado_por_id", sa.BigInteger(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enviado_por_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_materiais_marca_clientes_organizacao_id", "materiais_marca_clientes", ["organizacao_id"])
    op.create_index("ix_materiais_marca_clientes_lead_id", "materiais_marca_clientes", ["lead_id"])
    op.create_index("ix_materiais_marca_clientes_arquivo_hash", "materiais_marca_clientes", ["arquivo_hash"])
    op.execute('ALTER TABLE "materiais_marca_clientes" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "materiais_marca_clientes" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "materiais_marca_clientes" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "materiais_marca_clientes" TO inpi_app')


def downgrade() -> None:
    op.drop_table("materiais_marca_clientes")

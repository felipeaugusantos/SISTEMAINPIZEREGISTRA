"""cadências de atendimento (sequência de passos)

Revision ID: zw99r4v0p286
Revises: zv88q3u9o175
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zw99r4v0p286"
down_revision: str | None = "zv88q3u9o175"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "cadencias",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cadencias_organizacao_id", "cadencias", ["organizacao_id"])
    op.create_index("ix_cadencias_ativo", "cadencias", ["ativo"])
    op.execute('ALTER TABLE "cadencias" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "cadencias" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "cadencias" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "cadencias" TO inpi_app')

    op.create_table(
        "cadencia_passos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("cadencia_id", sa.BigInteger(), nullable=False),
        sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dia", sa.Integer(), server_default="0", nullable=False),
        sa.Column("canal", sa.String(20), server_default="outro", nullable=False),
        sa.Column("titulo", sa.String(180), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cadencia_id"], ["cadencias.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cadencia_passos_organizacao_id", "cadencia_passos", ["organizacao_id"])
    op.create_index("ix_cadencia_passos_cadencia_id", "cadencia_passos", ["cadencia_id"])
    op.execute('ALTER TABLE "cadencia_passos" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "cadencia_passos" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "cadencia_passos" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "cadencia_passos" TO inpi_app')


def downgrade() -> None:
    op.drop_table("cadencia_passos")
    op.drop_table("cadencias")

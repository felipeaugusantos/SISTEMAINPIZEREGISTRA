"""Add preventive RPI surveillance and collision review."""

import sqlalchemy as sa
from alembic import op

revision = "acd77v2o0y74"
down_revision = "acc66u1n9x63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("preferencias_vigilancia", sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("organizacao_id", sa.BigInteger(), nullable=False), sa.Column("cliente_id", sa.BigInteger(), nullable=False), sa.Column("frequencia", sa.String(20), nullable=False, server_default="semanal"), sa.Column("classes_nice", sa.JSON(), nullable=False), sa.Column("codigos_viena", sa.JSON(), nullable=False), sa.Column("canais", sa.JSON(), nullable=False), sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["cliente_id"], ["clientes_portal.id"], ondelete="CASCADE"), sa.UniqueConstraint("organizacao_id", "cliente_id", name="uq_preferencia_vigilancia_cliente"))
    op.create_index("ix_preferencias_vigilancia_cliente_id", "preferencias_vigilancia", ["cliente_id"])
    op.create_table("colidencias_vigilancia", sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("organizacao_id", sa.BigInteger(), nullable=False), sa.Column("cliente_id", sa.BigInteger(), nullable=False), sa.Column("processo_id", sa.BigInteger(), nullable=False), sa.Column("rpi_numero", sa.Integer()), sa.Column("score_risco", sa.Float(), nullable=False, server_default="0"), sa.Column("status", sa.String(20), nullable=False, server_default="pendente"), sa.Column("evidencias", sa.JSON(), nullable=False), sa.Column("justificativa", sa.Text(), nullable=False), sa.Column("revisado_por", sa.BigInteger()), sa.Column("revisado_em", sa.DateTime(timezone=True)), sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["cliente_id"], ["clientes_portal.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["processo_id"], ["processos.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["revisado_por"], ["usuarios_operacoes.id"], ondelete="SET NULL"), sa.UniqueConstraint("organizacao_id", "cliente_id", "processo_id", name="uq_colidencia_vigilancia_processo"))
    for col in ("cliente_id", "processo_id", "status", "rpi_numero"):
        op.create_index(f"ix_colidencias_vigilancia_{col}", "colidencias_vigilancia", [col])


def downgrade() -> None:
    for col in ("rpi_numero", "status", "processo_id", "cliente_id"):
        op.drop_index(f"ix_colidencias_vigilancia_{col}", table_name="colidencias_vigilancia")
    op.drop_table("colidencias_vigilancia")
    op.drop_index("ix_preferencias_vigilancia_cliente_id", table_name="preferencias_vigilancia")
    op.drop_table("preferencias_vigilancia")

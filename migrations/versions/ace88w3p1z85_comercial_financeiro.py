"""Add service catalog, contracts and financial linkage."""

import sqlalchemy as sa
from alembic import op

revision = "ace88w3p1z85"
down_revision = "acd77v2o0y74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lancamentos_financeiros", sa.Column("lead_id", sa.BigInteger()))
    op.add_column("lancamentos_financeiros", sa.Column("processo_id", sa.BigInteger()))
    op.add_column("lancamentos_financeiros", sa.Column("idempotency_key", sa.String(120)))
    op.create_index("ix_lancamentos_financeiros_lead_id", "lancamentos_financeiros", ["lead_id"])
    op.create_index("ix_lancamentos_financeiros_processo_id", "lancamentos_financeiros", ["processo_id"])
    op.create_index("ix_lancamentos_financeiros_idempotency_key", "lancamentos_financeiros", ["idempotency_key"], unique=True)
    op.create_foreign_key("fk_lancamento_lead", "lancamentos_financeiros", "leads", ["lead_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_lancamento_processo", "lancamentos_financeiros", "processos", ["processo_id"], ["id"], ondelete="SET NULL")
    op.create_table("servicos_financeiros", sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("organizacao_id", sa.BigInteger(), nullable=False), sa.Column("codigo", sa.String(50), nullable=False), sa.Column("nome", sa.String(180), nullable=False), sa.Column("descricao", sa.Text()), sa.Column("valor", sa.Numeric(14, 2), nullable=False), sa.Column("recorrente", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"), sa.UniqueConstraint("organizacao_id", "codigo", name="uq_servico_financeiro_codigo"))
    op.create_index("ix_servicos_financeiros_codigo", "servicos_financeiros", ["codigo"])
    op.create_table("contratacoes_servicos", sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("organizacao_id", sa.BigInteger(), nullable=False), sa.Column("servico_id", sa.BigInteger(), nullable=False), sa.Column("lead_id", sa.BigInteger()), sa.Column("processo_id", sa.BigInteger()), sa.Column("lancamento_id", sa.BigInteger(), unique=True), sa.Column("status", sa.String(20), nullable=False, server_default="contratada"), sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["servico_id"], ["servicos_financeiros.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["processo_id"], ["processos.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["lancamento_id"], ["lancamentos_financeiros.id"], ondelete="SET NULL"))
    op.create_index("ix_contratacoes_servicos_lead_id", "contratacoes_servicos", ["lead_id"])


def downgrade() -> None:
    op.drop_table("contratacoes_servicos")
    op.drop_index("ix_servicos_financeiros_codigo", table_name="servicos_financeiros")
    op.drop_table("servicos_financeiros")
    op.drop_constraint("fk_lancamento_processo", "lancamentos_financeiros", type_="foreignkey")
    op.drop_constraint("fk_lancamento_lead", "lancamentos_financeiros", type_="foreignkey")
    op.drop_index("ix_lancamentos_financeiros_idempotency_key", table_name="lancamentos_financeiros")
    op.drop_index("ix_lancamentos_financeiros_processo_id", table_name="lancamentos_financeiros")
    op.drop_index("ix_lancamentos_financeiros_lead_id", table_name="lancamentos_financeiros")
    op.drop_column("lancamentos_financeiros", "idempotency_key")
    op.drop_column("lancamentos_financeiros", "processo_id")
    op.drop_column("lancamentos_financeiros", "lead_id")

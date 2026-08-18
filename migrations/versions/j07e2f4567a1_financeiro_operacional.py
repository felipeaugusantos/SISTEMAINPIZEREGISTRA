"""financeiro operacional: propostas, GRUs, recibos e renovacoes"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "j07e2f4567a1"
down_revision: str | Sequence[str] | None = "i96d1e2f3456"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("lancamentos_financeiros", sa.Column("proposta_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_lancamentos_financeiros_proposta_id", "lancamentos_financeiros", ["proposta_id"])
    op.create_foreign_key("fk_lancamento_proposta", "lancamentos_financeiros", "propostas_comerciais", ["proposta_id"], ["id"], ondelete="SET NULL")
    op.add_column("contratacoes_servicos", sa.Column("proposta_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_contratacoes_servicos_proposta_id", "contratacoes_servicos", ["proposta_id"])
    op.create_foreign_key("fk_contratacao_proposta", "contratacoes_servicos", "propostas_comerciais", ["proposta_id"], ["id"], ondelete="SET NULL")
    op.add_column("guias_inpi", sa.Column("processo_id", sa.BigInteger(), nullable=True))
    op.add_column("guias_inpi", sa.Column("proposta_id", sa.BigInteger(), nullable=True))
    op.add_column("guias_inpi", sa.Column("lancamento_id", sa.BigInteger(), nullable=True))
    for col in ("processo_id", "proposta_id", "lancamento_id"):
        op.create_index(f"ix_guias_inpi_{col}", "guias_inpi", [col])
    op.create_foreign_key("fk_guia_processo", "guias_inpi", "processos", ["processo_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_guia_proposta", "guias_inpi", "propostas_comerciais", ["proposta_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_guia_lancamento", "guias_inpi", "lancamentos_financeiros", ["lancamento_id"], ["id"], ondelete="SET NULL")
    op.create_table(
        "recibos_financeiros",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("parcela_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.String(60), nullable=False),
        sa.Column("emitido_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dados", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("numero"), sa.UniqueConstraint("organizacao_id", "parcela_id", name="uq_recibo_financeiro_parcela"),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parcela_id"], ["parcelas_financeiras.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_recibos_financeiros_parcela_id", "recibos_financeiros", ["parcela_id"])
    op.create_index("ix_recibos_financeiros_numero", "recibos_financeiros", ["numero"])
    op.create_table(
        "renovacoes_financeiras",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False), sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("processo_id", sa.BigInteger(), nullable=False), sa.Column("tipo", sa.String(30), nullable=False, server_default="renovacao"),
        sa.Column("referencia", sa.String(40), nullable=False), sa.Column("vencimento", sa.Date(), nullable=False), sa.Column("status", sa.String(20), nullable=False, server_default="pendente"),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "processo_id", "tipo", "referencia", name="uq_renovacao_financeira"),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["processo_id"], ["processos.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_renovacoes_financeiras_vencimento", "renovacoes_financeiras", ["vencimento"])

def downgrade() -> None:
    op.drop_index("ix_renovacoes_financeiras_vencimento", table_name="renovacoes_financeiras")
    op.drop_table("renovacoes_financeiras")
    op.drop_index("ix_recibos_financeiros_numero", table_name="recibos_financeiros")
    op.drop_index("ix_recibos_financeiros_parcela_id", table_name="recibos_financeiros")
    op.drop_table("recibos_financeiros")
    for name, table in (("fk_guia_lancamento", "guias_inpi"), ("fk_guia_proposta", "guias_inpi"), ("fk_guia_processo", "guias_inpi"), ("fk_contratacao_proposta", "contratacoes_servicos"), ("fk_lancamento_proposta", "lancamentos_financeiros")):
        op.drop_constraint(name, table, type_="foreignkey")
    for col in ("lancamento_id", "proposta_id", "processo_id"):
        op.drop_index(f"ix_guias_inpi_{col}", table_name="guias_inpi")
    op.drop_column("guias_inpi", "lancamento_id")
    op.drop_column("guias_inpi", "proposta_id")
    op.drop_column("guias_inpi", "processo_id")
    op.drop_index("ix_contratacoes_servicos_proposta_id", table_name="contratacoes_servicos")
    op.drop_column("contratacoes_servicos", "proposta_id")
    op.drop_index("ix_lancamentos_financeiros_proposta_id", table_name="lancamentos_financeiros")
    op.drop_column("lancamentos_financeiros", "proposta_id")

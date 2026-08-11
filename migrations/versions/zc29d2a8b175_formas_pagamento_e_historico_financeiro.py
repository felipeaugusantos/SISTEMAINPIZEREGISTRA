"""formas de pagamento e historico financeiro

Revision ID: zc29d2a8b175
Revises: zb28c1f7a064
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zc29d2a8b175"
down_revision: str | None = "zb28c1f7a064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def _tenant_table(nome: str) -> None:
    op.execute(f'ALTER TABLE "{nome}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{nome}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{nome}" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{nome}" TO inpi_app')


def upgrade() -> None:
    op.create_table(
        "formas_pagamento_financeiras",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("tipo", sa.String(30), server_default="outro", nullable=False),
        sa.Column("permite_parcelamento", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("maximo_parcelas", sa.Integer(), server_default="1", nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "nome", name="uq_forma_pagamento_financeira_org"),
    )
    for coluna in ("organizacao_id", "nome", "tipo", "ativo"):
        op.create_index(f"ix_formas_pagamento_financeiras_{coluna}", "formas_pagamento_financeiras", [coluna])
    _tenant_table("formas_pagamento_financeiras")

    op.add_column("lancamentos_financeiros", sa.Column("forma_pagamento_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_lancamentos_forma_pagamento",
        "lancamentos_financeiros",
        "formas_pagamento_financeiras",
        ["forma_pagamento_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_lancamentos_financeiros_forma_pagamento_id", "lancamentos_financeiros", ["forma_pagamento_id"])
    op.add_column("parcelas_financeiras", sa.Column("forma_pagamento_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_parcelas_forma_pagamento",
        "parcelas_financeiras",
        "formas_pagamento_financeiras",
        ["forma_pagamento_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_parcelas_financeiras_forma_pagamento_id", "parcelas_financeiras", ["forma_pagamento_id"])

    op.create_table(
        "historicos_financeiros",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lancamento_id", sa.BigInteger(), nullable=False),
        sa.Column("parcela_id", sa.BigInteger(), nullable=True),
        sa.Column("acao", sa.String(30), nullable=False),
        sa.Column("ator", sa.String(254), nullable=False),
        sa.Column("descricao", sa.String(500), nullable=False),
        sa.Column("detalhes", sa.JSON(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lancamento_id"], ["lancamentos_financeiros.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parcela_id"], ["parcelas_financeiras.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("organizacao_id", "lancamento_id", "parcela_id", "acao", "ator", "criado_em"):
        op.create_index(f"ix_historicos_financeiros_{coluna}", "historicos_financeiros", [coluna])
    _tenant_table("historicos_financeiros")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")
    op.execute("""
        INSERT INTO formas_pagamento_financeiras
            (organizacao_id, nome, tipo, permite_parcelamento, maximo_parcelas, ativo)
        SELECT o.id, padrao.nome, padrao.tipo, padrao.parcelavel, padrao.maximo, true
        FROM organizacoes o
        CROSS JOIN (VALUES
            ('PIX', 'pix', false, 1),
            ('Boleto', 'boleto', true, 12),
            ('Transferência bancária', 'transferencia', false, 1),
            ('Cartão de crédito', 'cartao_credito', true, 12),
            ('Cartão de débito', 'cartao_debito', false, 1),
            ('Dinheiro', 'dinheiro', false, 1)
        ) AS padrao(nome, tipo, parcelavel, maximo)
        ON CONFLICT (organizacao_id, nome) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("historicos_financeiros")
    op.drop_index("ix_parcelas_financeiras_forma_pagamento_id", table_name="parcelas_financeiras")
    op.drop_constraint("fk_parcelas_forma_pagamento", "parcelas_financeiras", type_="foreignkey")
    op.drop_column("parcelas_financeiras", "forma_pagamento_id")
    op.drop_index("ix_lancamentos_financeiros_forma_pagamento_id", table_name="lancamentos_financeiros")
    op.drop_constraint("fk_lancamentos_forma_pagamento", "lancamentos_financeiros", type_="foreignkey")
    op.drop_column("lancamentos_financeiros", "forma_pagamento_id")
    op.drop_table("formas_pagamento_financeiras")

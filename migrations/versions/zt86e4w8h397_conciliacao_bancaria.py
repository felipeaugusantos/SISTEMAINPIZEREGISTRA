"""conciliacao bancaria: extratos OFX e transacoes (FASE7-3)

Revision ID: zt86e4w8h397
Revises: ys75d3v7g286

Achado FASE7-3 da auditoria (04/09/2026): nao existia conciliacao
bancaria. extratos_bancarios registra cada arquivo OFX importado;
transacoes_bancarias guarda cada linha (credito/debito), com fitid unico
por organizacao (reimportar o mesmo extrato nunca duplica) e vinculo
opcional a uma parcela_financeira quando conciliada.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zt86e4w8h397"
down_revision: str | None = "ys75d3v7g286"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def _rls(tabela: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{tabela}" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{tabela}" TO inpi_app')


def upgrade() -> None:
    op.create_table(
        "extratos_bancarios",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("nome_arquivo", sa.String(length=255), nullable=False),
        sa.Column("total_transacoes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("importado_por", sa.String(length=254), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extratos_bancarios_organizacao_id", "extratos_bancarios", ["organizacao_id"])
    op.create_index("ix_extratos_bancarios_criado_em", "extratos_bancarios", ["criado_em"])

    op.create_table(
        "transacoes_bancarias",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("extrato_id", sa.BigInteger(), nullable=False),
        sa.Column("fitid", sa.String(length=120), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("valor", sa.Numeric(14, 2), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("descricao", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pendente"),
        sa.Column("parcela_id", sa.BigInteger(), nullable=True),
        sa.Column("conciliado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conciliado_por", sa.String(length=254), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extrato_id"], ["extratos_bancarios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parcela_id"], ["parcelas_financeiras.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "fitid", name="uq_transacao_bancaria_fitid"),
    )
    op.create_index("ix_transacoes_bancarias_organizacao_id", "transacoes_bancarias", ["organizacao_id"])
    op.create_index("ix_transacoes_bancarias_extrato_id", "transacoes_bancarias", ["extrato_id"])
    op.create_index("ix_transacoes_bancarias_fitid", "transacoes_bancarias", ["fitid"])
    op.create_index("ix_transacoes_bancarias_data", "transacoes_bancarias", ["data"])
    op.create_index("ix_transacoes_bancarias_tipo", "transacoes_bancarias", ["tipo"])
    op.create_index("ix_transacoes_bancarias_status", "transacoes_bancarias", ["status"])
    op.create_index("ix_transacoes_bancarias_parcela_id", "transacoes_bancarias", ["parcela_id"])
    op.create_index("ix_transacoes_bancarias_criado_em", "transacoes_bancarias", ["criado_em"])

    _rls("extratos_bancarios")
    _rls("transacoes_bancarias")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("transacoes_bancarias")
    op.drop_table("extratos_bancarios")

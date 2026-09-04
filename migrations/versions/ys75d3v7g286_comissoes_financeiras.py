"""comissao de operador sobre receita (FASE7-7)

Revision ID: ys75d3v7g286
Revises: xr64c2u6f175

Achado FASE7-7 da auditoria (04/09/2026): nenhuma comissao de
vendedor/operador existia no sistema. usuarios_operacoes ganha
percentual_comissao (nulo = sem comissionamento, retrocompativel);
comissoes_financeiras registra uma linha por parcela baixada de um
lancamento "receber" vinculado a um Lead com responsavel comissionado.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ys75d3v7g286"
down_revision: str | None = "xr64c2u6f175"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.add_column("usuarios_operacoes", sa.Column("percentual_comissao", sa.Numeric(5, 2), nullable=True))

    op.create_table(
        "comissoes_financeiras",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("lancamento_id", sa.BigInteger(), nullable=False),
        sa.Column("parcela_id", sa.BigInteger(), nullable=False),
        sa.Column("valor_base", sa.Numeric(14, 2), nullable=False),
        sa.Column("percentual", sa.Numeric(5, 2), nullable=False),
        sa.Column("valor_comissao", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pendente"),
        sa.Column("pago_em", sa.Date(), nullable=True),
        sa.Column("pago_por", sa.String(length=254), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios_operacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lancamento_id"], ["lancamentos_financeiros.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parcela_id"], ["parcelas_financeiras.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parcela_id", name="uq_comissao_financeira_parcela"),
    )
    op.create_index("ix_comissoes_financeiras_organizacao_id", "comissoes_financeiras", ["organizacao_id"])
    op.create_index("ix_comissoes_financeiras_usuario_id", "comissoes_financeiras", ["usuario_id"])
    op.create_index("ix_comissoes_financeiras_lancamento_id", "comissoes_financeiras", ["lancamento_id"])
    op.create_index("ix_comissoes_financeiras_parcela_id", "comissoes_financeiras", ["parcela_id"])
    op.create_index("ix_comissoes_financeiras_status", "comissoes_financeiras", ["status"])
    op.create_index("ix_comissoes_financeiras_criado_em", "comissoes_financeiras", ["criado_em"])

    op.execute('ALTER TABLE "comissoes_financeiras" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "comissoes_financeiras" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "comissoes_financeiras" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "comissoes_financeiras" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("comissoes_financeiras")
    op.drop_column("usuarios_operacoes", "percentual_comissao")

"""recursos financeiros e jurídicos para escritórios e departamentos corporativos

Revision ID: aa52c9d7e813
Revises: aa41b8c6d702
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "aa52c9d7e813"
down_revision: str | None = "aa41b8c6d702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant ON {table} USING "
        "(current_setting('app.superadmin', true) = 'true' OR "
        "organizacao_id = current_setting('app.organizacao_id', true)::bigint)"
    )


def upgrade() -> None:
    op.add_column("usuarios_operacoes", sa.Column("departamento", sa.String(80), nullable=True))
    op.create_index("ix_usuarios_operacoes_departamento", "usuarios_operacoes", ["departamento"])
    op.create_table(
        "departamentos_financeiros",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger,
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("codigo", sa.String(50), nullable=False),
        sa.Column("nome", sa.String(150), nullable=False),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organizacao_id", "codigo", name="uq_departamento_financeiro_org"),
    )
    op.create_table(
        "centros_custo_financeiros",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger,
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "departamento_id",
            sa.BigInteger,
            sa.ForeignKey("departamentos_financeiros.id", ondelete="SET NULL"),
        ),
        sa.Column("codigo", sa.String(50), nullable=False),
        sa.Column("nome", sa.String(150), nullable=False),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organizacao_id", "codigo", name="uq_centro_custo_financeiro_org"),
    )
    op.create_table(
        "fornecedores_juridicos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger,
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nome", sa.String(180), nullable=False),
        sa.Column("documento", sa.String(30)),
        sa.Column("email", sa.String(254)),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "organizacao_id", "documento", name="uq_fornecedor_juridico_org_documento"
        ),
    )
    op.create_table(
        "contratos_juridicos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger,
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("titulo", sa.String(180), nullable=False),
        sa.Column(
            "fornecedor_id",
            sa.BigInteger,
            sa.ForeignKey("fornecedores_juridicos.id", ondelete="SET NULL"),
        ),
        sa.Column("processo_id", sa.BigInteger, sa.ForeignKey("processos.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(30), nullable=False, server_default="ativo"),
        sa.Column("vigencia_inicio", sa.Date),
        sa.Column("vigencia_fim", sa.Date),
        sa.Column("valor", sa.Numeric(14, 2)),
        sa.Column("documento_hash", sa.String(64)),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "custos_juridicos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger,
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "centro_custo_id",
            sa.BigInteger,
            sa.ForeignKey("centros_custo_financeiros.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "departamento_id",
            sa.BigInteger,
            sa.ForeignKey("departamentos_financeiros.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "fornecedor_id",
            sa.BigInteger,
            sa.ForeignKey("fornecedores_juridicos.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "contrato_id",
            sa.BigInteger,
            sa.ForeignKey("contratos_juridicos.id", ondelete="SET NULL"),
        ),
        sa.Column("processo_id", sa.BigInteger, sa.ForeignKey("processos.id", ondelete="SET NULL")),
        sa.Column("categoria", sa.String(30), nullable=False),
        sa.Column("descricao", sa.String(240), nullable=False),
        sa.Column("valor", sa.Numeric(14, 2), nullable=False),
        sa.Column("responsaveis", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column(
            "criado_por_id",
            sa.BigInteger,
            sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "organizacao_id", "idempotency_key", name="uq_custo_juridico_idempotencia"
        ),
    )
    op.create_table(
        "webhooks_financeiros",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger,
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("referencia", sa.String(150), nullable=False),
        sa.Column("evento", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="recebido"),
        sa.Column("tentativas", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "organizacao_id", "referencia", name="uq_webhook_financeiro_org_referencia"
        ),
    )
    for table in (
        "departamentos_financeiros",
        "centros_custo_financeiros",
        "fornecedores_juridicos",
        "contratos_juridicos",
        "custos_juridicos",
        "webhooks_financeiros",
    ):
        _tenant(table)


def downgrade() -> None:
    for table in (
        "webhooks_financeiros",
        "custos_juridicos",
        "contratos_juridicos",
        "fornecedores_juridicos",
        "centros_custo_financeiros",
        "departamentos_financeiros",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.drop_index("ix_usuarios_operacoes_departamento", table_name="usuarios_operacoes")
    op.drop_column("usuarios_operacoes", "departamento")

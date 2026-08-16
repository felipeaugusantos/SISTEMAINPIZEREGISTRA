"""adiciona propostas comerciais versionadas

Revision ID: za43u6v4t630
Revises: zz32u7y3s519
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "za43u6v4t630"
down_revision: tuple[str, str] = ("ac65w9a5u731", "zz32u7y3s519")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "propostas_comerciais",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.String(length=40), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="rascunho"),
        sa.Column("validade_em", sa.Date(), nullable=True),
        sa.Column("marca", sa.String(length=200), nullable=True),
        sa.Column("classes", sa.String(length=200), nullable=True),
        sa.Column("escopo", sa.Text(), nullable=False),
        sa.Column("honorarios", sa.Numeric(12, 2), nullable=True),
        sa.Column("taxa_gru", sa.Numeric(12, 2), nullable=True),
        sa.Column("condicoes_pagamento", sa.Text(), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("dados", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("enviado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aceito_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_por", sa.BigInteger(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "numero", name="uq_proposta_org_numero"),
    )
    op.create_index("ix_propostas_comerciais_organizacao_id", "propostas_comerciais", ["organizacao_id"])
    op.create_index("ix_propostas_comerciais_lead_id", "propostas_comerciais", ["lead_id"])
    op.create_index("ix_propostas_comerciais_numero", "propostas_comerciais", ["numero"])
    op.create_index("ix_propostas_comerciais_status", "propostas_comerciais", ["status"])


def downgrade() -> None:
    op.drop_index("ix_propostas_comerciais_status", table_name="propostas_comerciais")
    op.drop_index("ix_propostas_comerciais_numero", table_name="propostas_comerciais")
    op.drop_index("ix_propostas_comerciais_lead_id", table_name="propostas_comerciais")
    op.drop_index("ix_propostas_comerciais_organizacao_id", table_name="propostas_comerciais")
    op.drop_table("propostas_comerciais")

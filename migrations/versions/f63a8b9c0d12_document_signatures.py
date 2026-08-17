"""add immutable proposal signature evidence"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f63a8b9c0d12"
down_revision: str | Sequence[str] | None = "e52f7a8b9c01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assinaturas_propostas_comerciais",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("proposta_id", sa.BigInteger(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("hash_documento", sa.String(64), nullable=False),
        sa.Column("cliente_id", sa.BigInteger(), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("assinado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("provedor", sa.String(30), nullable=False, server_default="interno"),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["proposta_id"], ["propostas_comerciais.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("organizacao_id", "proposta_id", "versao", "hash_documento", "cliente_id", "assinado_em"):
        op.create_index(f"ix_assinaturas_propostas_comerciais_{coluna}", "assinaturas_propostas_comerciais", [coluna])


def downgrade() -> None:
    for coluna in ("assinado_em", "cliente_id", "hash_documento", "versao", "proposta_id", "organizacao_id"):
        op.drop_index(f"ix_assinaturas_propostas_comerciais_{coluna}", table_name="assinaturas_propostas_comerciais")
    op.drop_table("assinaturas_propostas_comerciais")

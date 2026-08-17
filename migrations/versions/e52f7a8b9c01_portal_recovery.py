"""add single-use customer portal recovery tokens"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e52f7a8b9c01"
down_revision: str | Sequence[str] | None = "d41e6f7a8b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recuperacoes_clientes_portal",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cliente_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["cliente_id"], ["clientes_portal.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    for coluna in ("cliente_id", "token_hash", "expira_em"):
        op.create_index(f"ix_recuperacoes_clientes_portal_{coluna}", "recuperacoes_clientes_portal", [coluna])


def downgrade() -> None:
    for coluna in ("expira_em", "token_hash", "cliente_id"):
        op.drop_index(f"ix_recuperacoes_clientes_portal_{coluna}", table_name="recuperacoes_clientes_portal")
    op.drop_table("recuperacoes_clientes_portal")

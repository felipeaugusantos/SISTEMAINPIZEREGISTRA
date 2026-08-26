"""Outbox transacional para eventos de domínio."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ac74e9f0b125"
down_revision: str | None = "zs66n0o3l842"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eventos_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("organizacao_id", sa.BigInteger(), sa.ForeignKey("organizacoes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evento_id", sa.BigInteger(), sa.ForeignKey("eventos_dominio.id", ondelete="SET NULL")),
        sa.Column("topico", sa.String(120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(180)),
        sa.Column("publicado_em", sa.DateTime(timezone=True)),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organizacao_id", "idempotency_key", name="uq_outbox_idempotencia"),
    )
    op.create_index("ix_eventos_outbox_organizacao_id", "eventos_outbox", ["organizacao_id"])
    op.create_index("ix_eventos_outbox_topico", "eventos_outbox", ["topico"])
    op.create_index("ix_eventos_outbox_publicado_em", "eventos_outbox", ["publicado_em"])
    op.create_index("ix_eventos_outbox_criado_em", "eventos_outbox", ["criado_em"])


def downgrade() -> None:
    op.drop_table("eventos_outbox")

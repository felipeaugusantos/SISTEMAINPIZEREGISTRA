"""remove eventos_outbox (escrita morta, nunca consumida)

Revision ID: ae35f6g7h963
Revises: ad24e5f6g852

Achado da auditoria do CRM: eventos_outbox era gravado a cada automacao do
CRM (mudanca de fase, cadencia, evento juridico/financeiro) mas nunca foi
lido por nenhum worker/consumidor -- nao ha nenhuma integracao externa
(webhook, fila) plugada nela. eventos_dominio (que E lido pela timeline do
lead) continua existindo normalmente; so a copia paralela que nunca era
usada foi removida.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ae35f6g7h963"
down_revision: str | None = "ad24e5f6g852"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("eventos_outbox")


def downgrade() -> None:
    op.create_table(
        "eventos_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "organizacao_id", sa.BigInteger(), sa.ForeignKey("organizacoes.id", ondelete="CASCADE"), nullable=False
        ),
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

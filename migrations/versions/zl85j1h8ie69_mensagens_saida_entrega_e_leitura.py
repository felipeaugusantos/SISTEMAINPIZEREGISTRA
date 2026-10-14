"""Mensagens de saída: horários de entrega e de leitura informados pelo gateway (recibos do WhatsApp).

Colunas anuláveis, sem valor padrão: linhas existentes seguem iguais.

Revision ID: zl85j1h8ie69
Revises: zk74i0g7hd58
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zl85j1h8ie69"
down_revision: str | None = "zk74i0g7hd58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mensagens_saida", sa.Column("entregue_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mensagens_saida", sa.Column("lida_em", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("mensagens_saida", "lida_em")
    op.drop_column("mensagens_saida", "entregue_em")

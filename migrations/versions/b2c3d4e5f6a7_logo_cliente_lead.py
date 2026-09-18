"""logo do cliente no lead (mao do personagem no portal)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6

Item 4/5 do pedido de melhorias do cliente final (17/09/2026): logo do
cliente exibida dinamicamente na mao do personagem no portal. Campo
transitorio (mesmo formato de Organizacao.branding["logo_asset"], mas por
lead), sem dado a migrar.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("logo_cliente", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "logo_cliente")

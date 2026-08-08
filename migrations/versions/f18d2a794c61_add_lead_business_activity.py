"""add lead business activity

Revision ID: f18d2a794c61
Revises: ef21bc58a036
Create Date: 2026-07-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f18d2a794c61"
down_revision: str | Sequence[str] | None = "ef21bc58a036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("atividade", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "atividade")

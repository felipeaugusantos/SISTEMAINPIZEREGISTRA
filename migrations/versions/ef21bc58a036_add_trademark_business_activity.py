"""add trademark business activity

Revision ID: ef21bc58a036
Revises: c91e84a57d20
Create Date: 2026-07-17 17:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ef21bc58a036"
down_revision: str | None = "c91e84a57d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pesquisas_marca", sa.Column("atividade", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("pesquisas_marca", "atividade")

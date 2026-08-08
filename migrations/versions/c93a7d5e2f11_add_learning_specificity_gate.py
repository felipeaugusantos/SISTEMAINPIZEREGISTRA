"""add learning specificity gate

Revision ID: c93a7d5e2f11
Revises: b82e6f4c9a10
Create Date: 2026-08-06 15:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c93a7d5e2f11"
down_revision: str | Sequence[str] | None = "b82e6f4c9a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "controle_aprendizado_marca",
        sa.Column(
            "minimo_especificidade",
            sa.Float(),
            nullable=False,
            server_default="0.7",
        ),
    )


def downgrade() -> None:
    op.drop_column("controle_aprendizado_marca", "minimo_especificidade")

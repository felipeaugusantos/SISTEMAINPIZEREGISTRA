"""track rpi imports

Revision ID: d9a4f21b730e
Revises: c82fd71a40e3
Create Date: 2026-07-16 01:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9a4f21b730e"
down_revision: str | None = "c82fd71a40e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rpi_importacoes",
        sa.Column("numero_rpi", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("registros_processados", sa.Integer(), nullable=False),
        sa.Column(
            "importado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("numero_rpi", "tipo"),
    )


def downgrade() -> None:
    op.drop_table("rpi_importacoes")

"""add high renown name

Revision ID: b71d8a20e964
Revises: a42c8e319b77
Create Date: 2026-07-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b71d8a20e964"
down_revision: str | Sequence[str] | None = "a42c8e319b77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("marcas_alto_renome", sa.Column("marca", sa.Text(), nullable=True))
    op.create_index(
        "ix_marcas_alto_renome_marca",
        "marcas_alto_renome",
        ["marca"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_marcas_alto_renome_marca", table_name="marcas_alto_renome")
    op.drop_column("marcas_alto_renome", "marca")

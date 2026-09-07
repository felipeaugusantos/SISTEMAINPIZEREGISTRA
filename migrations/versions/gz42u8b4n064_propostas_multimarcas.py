"""Permite resumos de propostas com varias marcas e classes.

Revision ID: gz42u8b4n064
Revises: gy31t7a3m953
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "gz42u8b4n064"
down_revision: str | None = "gy31t7a3m953"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("propostas_comerciais", "marca", existing_type=sa.String(length=200), type_=sa.Text())
    op.alter_column("propostas_comerciais", "classes", existing_type=sa.String(length=200), type_=sa.Text())


def downgrade() -> None:
    op.alter_column("propostas_comerciais", "marca", existing_type=sa.Text(), type_=sa.String(length=200))
    op.alter_column("propostas_comerciais", "classes", existing_type=sa.Text(), type_=sa.String(length=200))

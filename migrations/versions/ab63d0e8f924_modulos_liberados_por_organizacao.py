"""Permissoes de modulos por organizacao SaaS.

Revision ID: ab63d0e8f924
Revises: aa52c9d7e813
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ab63d0e8f924"
down_revision: str | None = "aa52c9d7e813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("organizacoes", sa.Column("modulos_liberados", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("organizacoes", "modulos_liberados")

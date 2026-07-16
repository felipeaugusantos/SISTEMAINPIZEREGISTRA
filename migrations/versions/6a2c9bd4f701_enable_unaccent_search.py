"""enable unaccent search

Revision ID: 6a2c9bd4f701
Revises: 18bfde207ed8
Create Date: 2026-07-15 21:10:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6a2c9bd4f701"
down_revision: str | None = "18bfde207ed8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    # A extensão pode ser compartilhada por outras aplicações no mesmo banco.
    pass

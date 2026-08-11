"""widen leads.status to fit longer StatusLead values

Revision ID: v22c5f0e1d80
Revises: u21b3e8a2c60
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v22c5f0e1d80"
down_revision: str | None = "u21b3e8a2c60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # O enum StatusLead ganhou valores maiores (ex.: 'proposta_enviada' = 16 chars),
    # mas a coluna continuava varchar(10), truncando na mudança de status.
    op.alter_column(
        "leads",
        "status",
        type_=sa.String(length=20),
        existing_type=sa.String(length=10),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "leads",
        "status",
        type_=sa.String(length=10),
        existing_type=sa.String(length=20),
        existing_nullable=False,
    )

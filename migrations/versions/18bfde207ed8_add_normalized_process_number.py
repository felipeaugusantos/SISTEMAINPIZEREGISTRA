"""add normalized process number

Revision ID: 18bfde207ed8
Revises: 5cb483d59152
Create Date: 2026-07-15 20:53:38.634010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "18bfde207ed8"
down_revision: str | None = "5cb483d59152"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processos", sa.Column("numero_normalizado", sa.String(length=30), nullable=True))
    op.execute(
        sa.text(
            "UPDATE processos SET numero_normalizado = "
            "upper(regexp_replace(numero, '[^A-Za-z0-9]', '', 'g'))"
        )
    )
    op.alter_column("processos", "numero_normalizado", nullable=False)
    op.create_index(
        op.f("ix_processos_numero_normalizado"),
        "processos",
        ["numero_normalizado"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_processos_numero_normalizado"), table_name="processos")
    op.drop_column("processos", "numero_normalizado")

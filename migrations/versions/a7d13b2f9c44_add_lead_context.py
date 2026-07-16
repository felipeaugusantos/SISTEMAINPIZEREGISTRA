"""add lead context

Revision ID: a7d13b2f9c44
Revises: f67b03d45c9a
Create Date: 2026-07-16 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d13b2f9c44"
down_revision: str | None = "f67b03d45c9a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("processo_numero", sa.String(length=50)))
    op.add_column(
        "leads",
        sa.Column(
            "origem",
            sa.String(length=30),
            server_default="resultados",
            nullable=False,
        ),
    )
    op.create_index("ix_leads_processo_numero", "leads", ["processo_numero"])
    op.create_index("ix_leads_origem", "leads", ["origem"])


def downgrade() -> None:
    op.drop_index("ix_leads_origem", table_name="leads")
    op.drop_index("ix_leads_processo_numero", table_name="leads")
    op.drop_column("leads", "origem")
    op.drop_column("leads", "processo_numero")

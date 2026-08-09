"""add complementary registrability data

Revision ID: n14f6d0e5a97
Revises: m03e5c9d4f86
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n14f6d0e5a97"
down_revision: str | None = "m03e5c9d4f86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pesquisas_marca",
        sa.Column(
            "dados_complementares_registrabilidade",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("pesquisas_marca", "dados_complementares_registrabilidade")

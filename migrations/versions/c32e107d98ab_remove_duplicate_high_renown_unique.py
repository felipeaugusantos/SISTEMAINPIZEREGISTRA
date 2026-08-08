"""remove duplicate high renown unique constraint

Revision ID: c32e107d98ab
Revises: b71d8a20e964
Create Date: 2026-07-17 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c32e107d98ab"
down_revision: str | Sequence[str] | None = "b71d8a20e964"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "marcas_alto_renome_numero_processo_normalizado_key",
        "marcas_alto_renome",
        type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "marcas_alto_renome_numero_processo_normalizado_key",
        "marcas_alto_renome",
        ["numero_processo_normalizado"],
    )

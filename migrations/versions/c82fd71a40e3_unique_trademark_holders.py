"""unique trademark holders

Revision ID: c82fd71a40e3
Revises: b4d39e0812c7
Create Date: 2026-07-16 00:45:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c82fd71a40e3"
down_revision: str | None = "b4d39e0812c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE titulares
        ADD CONSTRAINT uq_titulares_nome_pais
        UNIQUE NULLS NOT DISTINCT (nome, pais)
        """
    )


def downgrade() -> None:
    op.drop_constraint("uq_titulares_nome_pais", "titulares", type_="unique")

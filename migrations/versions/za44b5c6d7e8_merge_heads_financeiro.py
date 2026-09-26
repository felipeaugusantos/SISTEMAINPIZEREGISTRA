"""Une as linhas de migração jurídico e registrabilidade antes das fases financeiras.

Revision ID: za44b5c6d7e8
Revises: i1d2e3f4g5h6, zz32u7y3s519
"""

from collections.abc import Sequence

revision: str = "za44b5c6d7e8"
down_revision: tuple[str, str] = ("i1d2e3f4g5h6", "zz32u7y3s519")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

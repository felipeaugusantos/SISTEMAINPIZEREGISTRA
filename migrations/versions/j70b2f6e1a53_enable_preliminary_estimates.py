"""enable preliminary estimates on the first client report

Revision ID: j70b2f6e1a53
Revises: i69a1e5d0f42
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j70b2f6e1a53"
down_revision: str | None = "i69a1e5d0f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "controle_aprendizado_marca",
        "exibir_cliente",
        existing_type=sa.Boolean(),
        server_default=sa.true(),
        existing_nullable=False,
    )
    op.execute(
        "UPDATE controle_aprendizado_marca "
        "SET inferencia_habilitada = true, rollout_percentual = 100, "
        "exibir_cliente = true, justificativa = "
        "'Estimativa preliminar exibida desde a primeira consulta; revisão humana não bloqueia'"
    )


def downgrade() -> None:
    op.execute("UPDATE controle_aprendizado_marca SET exibir_cliente = false")
    op.alter_column(
        "controle_aprendizado_marca",
        "exibir_cliente",
        existing_type=sa.Boolean(),
        server_default=sa.false(),
        existing_nullable=False,
    )

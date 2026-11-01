"""Prazo para novas senhas temporárias, preservando credenciais existentes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zm96k2i9jf70"
down_revision: str | None = "zl85j1h8ie69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usuarios_operacoes", sa.Column("senha_temporaria_expira_em", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("usuarios_operacoes", "senha_temporaria_expira_em")

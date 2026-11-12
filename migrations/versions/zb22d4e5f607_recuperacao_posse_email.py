"""recuperação de acesso à pesquisa com prova de posse do e-mail

Revision ID: zb22d4e5f607
Revises: za11b2c3d4e5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zb22d4e5f607"
down_revision: str | None = "za11b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pesquisas_marca",
        sa.Column("recuperacao_codigo_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "pesquisas_marca",
        sa.Column("recuperacao_codigo_expira_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pesquisas_marca",
        sa.Column("recuperacao_codigo_tentativas", sa.Integer(), nullable=True),
    )
    op.add_column(
        "pesquisas_marca",
        sa.Column("recuperacao_codigo_enviado_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pesquisas_marca", "recuperacao_codigo_enviado_em")
    op.drop_column("pesquisas_marca", "recuperacao_codigo_tentativas")
    op.drop_column("pesquisas_marca", "recuperacao_codigo_expira_em")
    op.drop_column("pesquisas_marca", "recuperacao_codigo_hash")

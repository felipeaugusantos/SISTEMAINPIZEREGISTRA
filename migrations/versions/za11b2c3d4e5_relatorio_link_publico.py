"""link público estável e revogável do relatório de pesquisa

Revision ID: za11b2c3d4e5
Revises: zm96k2i9jf70
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "za11b2c3d4e5"
down_revision: str | None = "zm96k2i9jf70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pesquisas_marca",
        sa.Column("relatorio_link_token_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "pesquisas_marca",
        sa.Column("relatorio_link_expira_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pesquisas_marca",
        sa.Column("confirmacao_email_enviado_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_pesquisas_marca_relatorio_link_token_hash",
        "pesquisas_marca",
        ["relatorio_link_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_pesquisas_marca_relatorio_link_token_hash", table_name="pesquisas_marca")
    op.drop_column("pesquisas_marca", "confirmacao_email_enviado_em")
    op.drop_column("pesquisas_marca", "relatorio_link_expira_em")
    op.drop_column("pesquisas_marca", "relatorio_link_token_hash")

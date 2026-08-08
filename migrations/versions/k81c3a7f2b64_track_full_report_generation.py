"""track the first generation of a full internal report

Revision ID: k81c3a7f2b64
Revises: j70b2f6e1a53
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k81c3a7f2b64"
down_revision: str | None = "j70b2f6e1a53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pesquisas_marca",
        sa.Column("relatorio_completo_gerado_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pesquisas_marca",
        sa.Column("relatorio_completo_gerado_por", sa.String(length=150), nullable=True),
    )
    op.create_index(
        "ix_pesquisas_marca_relatorio_completo_gerado_em",
        "pesquisas_marca",
        ["relatorio_completo_gerado_em"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pesquisas_marca_relatorio_completo_gerado_em",
        table_name="pesquisas_marca",
    )
    op.drop_column("pesquisas_marca", "relatorio_completo_gerado_por")
    op.drop_column("pesquisas_marca", "relatorio_completo_gerado_em")

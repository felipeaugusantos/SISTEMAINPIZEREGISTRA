"""remove unicidade redundante das retribuicoes do INPI

Revision ID: zp22k7o3i519
Revises: zo11j6n2h408
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zp22k7o3i519"
down_revision: str | None = "zo11j6n2h408"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # O indice ix_retribuicoes_inpi_servico continua sendo unico. A constraint
    # removida duplicava a mesma garantia e fazia o schema divergir do modelo.
    op.drop_constraint(
        "uq_retribuicao_inpi_servico",
        "retribuicoes_inpi",
        type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_retribuicao_inpi_servico",
        "retribuicoes_inpi",
        ["servico"],
    )

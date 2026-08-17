"""Add covering order index for RPI consultation.

Revision ID: aaa33r8k6u30
Revises: zz32u7y3s519, zd76x9y7w963
"""

from alembic import op

revision = "aaa33r8k6u30"
down_revision = ("zz32u7y3s519", "zd76x9y7w963")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_movimentacoes_rpi_consulta",
        "movimentacoes",
        ["numero_rpi", "data_rpi", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_movimentacoes_rpi_consulta", table_name="movimentacoes")

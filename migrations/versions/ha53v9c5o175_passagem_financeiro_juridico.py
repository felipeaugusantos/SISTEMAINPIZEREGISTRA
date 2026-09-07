"""Registra o recebimento da proposta pela operacao juridica.

Revision ID: ha53v9c5o175
Revises: gz42u8b4n064
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ha53v9c5o175"
down_revision: str | None = "gz42u8b4n064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("propostas_comerciais", sa.Column("juridico_recebido_em", sa.DateTime(timezone=True)))
    op.add_column("propostas_comerciais", sa.Column("juridico_recebido_por_id", sa.BigInteger()))
    op.add_column("propostas_comerciais", sa.Column("juridico_recebido_por", sa.String(length=254)))
    op.create_foreign_key(
        "fk_propostas_juridico_recebido_por",
        "propostas_comerciais",
        "usuarios_operacoes",
        ["juridico_recebido_por_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_propostas_comerciais_juridico_recebido_em",
        "propostas_comerciais",
        ["juridico_recebido_em"],
    )
    op.create_index(
        "ix_propostas_comerciais_juridico_recebido_por_id",
        "propostas_comerciais",
        ["juridico_recebido_por_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_propostas_comerciais_juridico_recebido_por_id", table_name="propostas_comerciais")
    op.drop_index("ix_propostas_comerciais_juridico_recebido_em", table_name="propostas_comerciais")
    op.drop_constraint("fk_propostas_juridico_recebido_por", "propostas_comerciais", type_="foreignkey")
    op.drop_column("propostas_comerciais", "juridico_recebido_por")
    op.drop_column("propostas_comerciais", "juridico_recebido_por_id")
    op.drop_column("propostas_comerciais", "juridico_recebido_em")

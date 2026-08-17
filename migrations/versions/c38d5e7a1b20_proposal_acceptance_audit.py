"""store proposal acceptance and payment audit metadata

Revision ID: c38d5e7a1b20
Revises: b27c4e8d9f10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c38d5e7a1b20"
down_revision: str | None = "b27c4e8d9f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "propostas_comerciais",
        sa.Column("public_aceito_ip_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "propostas_comerciais",
        sa.Column("pagamento_confirmado_por_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "propostas_comerciais",
        sa.Column("pagamento_confirmado_por", sa.String(length=254), nullable=True),
    )
    op.add_column(
        "propostas_comerciais",
        sa.Column("pagamento_confirmado_ip_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_propostas_comerciais_pagamento_confirmado_por_id",
        "propostas_comerciais",
        ["pagamento_confirmado_por_id"],
    )
    op.create_foreign_key(
        "fk_proposta_pagamento_confirmado_por",
        "propostas_comerciais",
        "usuarios_operacoes",
        ["pagamento_confirmado_por_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_proposta_pagamento_confirmado_por",
        "propostas_comerciais",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_propostas_comerciais_pagamento_confirmado_por_id",
        table_name="propostas_comerciais",
    )
    for coluna in (
        "pagamento_confirmado_ip_hash",
        "pagamento_confirmado_por",
        "pagamento_confirmado_por_id",
        "public_aceito_ip_hash",
    ):
        op.drop_column("propostas_comerciais", coluna)

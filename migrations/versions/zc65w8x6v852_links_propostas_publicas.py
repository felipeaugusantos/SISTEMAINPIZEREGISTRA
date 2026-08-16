"""adiciona links publicos e aceite de propostas

Revision ID: zc65w8x6v852
Revises: zb54v7w5u741
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zc65w8x6v852"
down_revision: str | None = "zb54v7w5u741"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("propostas_comerciais", sa.Column("public_token_hash", sa.String(64), nullable=True))
    op.add_column("propostas_comerciais", sa.Column("public_token_expira_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("propostas_comerciais", sa.Column("public_aceito_em", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_propostas_comerciais_public_token_hash", "propostas_comerciais", ["public_token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_propostas_comerciais_public_token_hash", table_name="propostas_comerciais")
    op.drop_column("propostas_comerciais", "public_aceito_em")
    op.drop_column("propostas_comerciais", "public_token_expira_em")
    op.drop_column("propostas_comerciais", "public_token_hash")

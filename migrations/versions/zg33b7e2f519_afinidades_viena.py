"""matriz de afinidade da Classificação de Viena (busca figurativa)

Revision ID: zg33b7e2f519
Revises: zf32a6d1e408
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zg33b7e2f519"
down_revision: str | None = "zf32a6d1e408"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "afinidades_viena",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("codigo_origem", sa.String(length=30), nullable=False, index=True),
        sa.Column("codigo_destino", sa.String(length=30), nullable=False, index=True),
        sa.Column("nivel", sa.String(length=20), nullable=False),
        sa.Column("justificativa", sa.Text(), nullable=False),
        sa.Column("versao", sa.String(length=20), nullable=False, server_default="inicial-2026"),
        sa.Column(
            "status_revisao", sa.String(length=20), nullable=False, server_default="pendente",
            index=True,
        ),
        sa.Column("revisor", sa.String(length=150), nullable=True),
        sa.Column("observacoes_revisao", sa.Text(), nullable=True),
        sa.Column("revisado_em", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("codigo_origem", "codigo_destino", name="uq_afinidades_viena_par"),
    )


def downgrade() -> None:
    op.drop_table("afinidades_viena")

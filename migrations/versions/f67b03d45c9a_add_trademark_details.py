"""add trademark details

Revision ID: f67b03d45c9a
Revises: e31a865cb120
Create Date: 2026-07-16 04:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f67b03d45c9a"
down_revision: str | None = "e31a865cb120"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processos", sa.Column("apresentacao", sa.String(length=100)))
    op.add_column("processos", sa.Column("natureza", sa.String(length=150)))
    op.add_column("processos", sa.Column("elemento_nominativo", sa.Text()))
    op.add_column("processos", sa.Column("procurador", sa.Text()))
    op.add_column("processos", sa.Column("imagem_url", sa.Text()))
    op.create_table(
        "classificacoes_marca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("processo_id", sa.BigInteger(), nullable=False),
        sa.Column("sistema", sa.String(length=20), nullable=False),
        sa.Column("codigo", sa.String(length=30), nullable=False),
        sa.Column("edicao", sa.String(length=20)),
        sa.Column("especificacao", sa.Text()),
        sa.Column("status", sa.String(length=255)),
        sa.ForeignKeyConstraint(["processo_id"], ["processos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "processo_id",
            "sistema",
            "codigo",
            name="uq_classificacoes_marca_processo_sistema_codigo",
        ),
    )
    op.create_index(
        "ix_classificacoes_marca_processo_id",
        "classificacoes_marca",
        ["processo_id"],
    )
    op.create_index("ix_classificacoes_marca_sistema", "classificacoes_marca", ["sistema"])
    op.create_index("ix_classificacoes_marca_codigo", "classificacoes_marca", ["codigo"])


def downgrade() -> None:
    op.drop_table("classificacoes_marca")
    op.drop_column("processos", "imagem_url")
    op.drop_column("processos", "procurador")
    op.drop_column("processos", "elemento_nominativo")
    op.drop_column("processos", "natureza")
    op.drop_column("processos", "apresentacao")

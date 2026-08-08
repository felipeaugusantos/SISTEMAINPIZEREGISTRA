"""add shadow risk engine

Revision ID: d84f1b29c703
Revises: c32e107d98ab
Create Date: 2026-07-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d84f1b29c703"
down_revision: str | Sequence[str] | None = "c32e107d98ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "avaliacoes_risco_marca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pesquisa_id", sa.String(length=36), nullable=False),
        sa.Column("versao_motor", sa.String(length=30), nullable=False),
        sa.Column(
            "modo",
            sa.String(length=20),
            nullable=False,
            server_default="sombra",
        ),
        sa.Column("pontuacao", sa.Integer(), nullable=False),
        sa.Column("nivel", sa.String(length=20), nullable=False),
        sa.Column("principais_conflitos", sa.JSON(), nullable=False),
        sa.Column("regras_aplicadas", sa.JSON(), nullable=False),
        sa.Column(
            "calculado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("nivel_humano", sa.String(length=20), nullable=True),
        sa.Column("avaliador", sa.String(length=150), nullable=True),
        sa.Column("observacoes_humanas", sa.Text(), nullable=True),
        sa.Column("avaliado_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["pesquisa_id"],
            ["pesquisas_marca.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_avaliacoes_risco_marca_pesquisa_id",
        "avaliacoes_risco_marca",
        ["pesquisa_id"],
        unique=True,
    )
    op.create_index(
        "ix_avaliacoes_risco_marca_versao_motor",
        "avaliacoes_risco_marca",
        ["versao_motor"],
        unique=False,
    )
    op.create_index(
        "ix_avaliacoes_risco_marca_modo",
        "avaliacoes_risco_marca",
        ["modo"],
        unique=False,
    )
    op.create_index(
        "ix_avaliacoes_risco_marca_nivel",
        "avaliacoes_risco_marca",
        ["nivel"],
        unique=False,
    )
    op.create_index(
        "ix_avaliacoes_risco_marca_nivel_humano",
        "avaliacoes_risco_marca",
        ["nivel_humano"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_avaliacoes_risco_marca_nivel_humano",
        table_name="avaliacoes_risco_marca",
    )
    op.drop_index("ix_avaliacoes_risco_marca_nivel", table_name="avaliacoes_risco_marca")
    op.drop_index("ix_avaliacoes_risco_marca_modo", table_name="avaliacoes_risco_marca")
    op.drop_index(
        "ix_avaliacoes_risco_marca_versao_motor",
        table_name="avaliacoes_risco_marca",
    )
    op.drop_index(
        "ix_avaliacoes_risco_marca_pesquisa_id",
        table_name="avaliacoes_risco_marca",
    )
    op.drop_table("avaliacoes_risco_marca")

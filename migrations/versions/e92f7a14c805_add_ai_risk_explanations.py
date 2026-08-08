"""add AI risk explanations

Revision ID: e92f7a14c805
Revises: d84f1b29c703
Create Date: 2026-07-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e92f7a14c805"
down_revision: str | Sequence[str] | None = "d84f1b29c703"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "explicacoes_risco_ia",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("avaliacao_risco_id", sa.BigInteger(), nullable=False),
        sa.Column("provedor", sa.String(length=30), nullable=False, server_default="openai"),
        sa.Column("modelo", sa.String(length=100), nullable=False),
        sa.Column("versao_prompt", sa.String(length=30), nullable=False),
        sa.Column("hash_entrada", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("entrada_estruturada", sa.JSON(), nullable=False),
        sa.Column("saida_estruturada", sa.JSON(), nullable=True),
        sa.Column("resposta_provedor_id", sa.String(length=100), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "revisao_obrigatoria",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("decisao_revisao", sa.String(length=20), nullable=True),
        sa.Column("revisor", sa.String(length=150), nullable=True),
        sa.Column("observacoes_revisao", sa.Text(), nullable=True),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revisado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["avaliacao_risco_id"],
            ["avaliacoes_risco_marca.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_explicacoes_risco_ia_avaliacao_risco_id",
        "explicacoes_risco_ia",
        ["avaliacao_risco_id"],
        unique=True,
    )
    op.create_index(
        "ix_explicacoes_risco_ia_hash_entrada",
        "explicacoes_risco_ia",
        ["hash_entrada"],
    )
    op.create_index(
        "ix_explicacoes_risco_ia_status",
        "explicacoes_risco_ia",
        ["status"],
    )
    op.create_index(
        "ix_explicacoes_risco_ia_decisao_revisao",
        "explicacoes_risco_ia",
        ["decisao_revisao"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_explicacoes_risco_ia_decisao_revisao",
        table_name="explicacoes_risco_ia",
    )
    op.drop_index("ix_explicacoes_risco_ia_status", table_name="explicacoes_risco_ia")
    op.drop_index(
        "ix_explicacoes_risco_ia_hash_entrada",
        table_name="explicacoes_risco_ia",
    )
    op.drop_index(
        "ix_explicacoes_risco_ia_avaliacao_risco_id",
        table_name="explicacoes_risco_ia",
    )
    op.drop_table("explicacoes_risco_ia")

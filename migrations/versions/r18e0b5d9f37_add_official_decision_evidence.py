"""add auditable official trademark decision evidence

Revision ID: r18e0b5d9f37
Revises: q17d9a4c8e26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r18e0b5d9f37"
down_revision: str | None = "q17d9a4c8e26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidencias_decisoes_marca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("rotulo_id", sa.BigInteger(), nullable=False),
        sa.Column("processo_numero", sa.String(length=30), nullable=False),
        sa.Column("cod_pedido", sa.String(length=30), nullable=True),
        sa.Column("fonte_url", sa.Text(), nullable=True),
        sa.Column("status_coleta", sa.String(length=30), server_default="pendente", nullable=False),
        sa.Column("status_http", sa.Integer(), nullable=True),
        sa.Column("tentativas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("despacho_texto", sa.Text(), nullable=True),
        sa.Column("numero_rpi", sa.Integer(), nullable=True),
        sa.Column("fundamento_sugerido", sa.String(length=50), nullable=True),
        sa.Column("confianca", sa.Float(), nullable=True),
        sa.Column("artigos", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column(
            "processos_citados",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("hash_conteudo", sa.String(length=64), nullable=True),
        sa.Column(
            "classificador_versao",
            sa.String(length=40),
            server_default="fundamento-inpi-1.0",
            nullable=False,
        ),
        sa.Column("coletado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["rotulo_id"], ["rotulos_historicos_marca.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rotulo_id"),
    )
    for column in (
        "rotulo_id",
        "processo_numero",
        "status_coleta",
        "numero_rpi",
        "fundamento_sugerido",
        "hash_conteudo",
        "classificador_versao",
    ):
        op.create_index(
            f"ix_evidencias_decisoes_marca_{column}",
            "evidencias_decisoes_marca",
            [column],
        )


def downgrade() -> None:
    op.drop_table("evidencias_decisoes_marca")

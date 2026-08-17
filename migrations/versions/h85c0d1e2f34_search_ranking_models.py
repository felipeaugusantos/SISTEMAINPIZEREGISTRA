"""version search ranking models and publication states"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h85c0d1e2f34"
down_revision: str | Sequence[str] | None = "g74b9c0d1e23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "modelos_ranking_busca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("versao", sa.String(60), nullable=False),
        sa.Column("algoritmo", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="SHADOW"),
        sa.Column("parametros", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("metricas", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("evidencias", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("dataset_version", sa.String(80), nullable=True),
        sa.Column("bloqueado_motivo", sa.Text(), nullable=True),
        sa.Column("publicado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publicado_por", sa.String(150), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("versao"),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
    )
    for coluna in ("organizacao_id", "versao", "status", "dataset_version", "publicado_em", "criado_em"):
        op.create_index(f"ix_modelos_ranking_busca_{coluna}", "modelos_ranking_busca", [coluna])


def downgrade() -> None:
    for coluna in ("criado_em", "publicado_em", "dataset_version", "status", "versao", "organizacao_id"):
        op.drop_index(f"ix_modelos_ranking_busca_{coluna}", table_name="modelos_ranking_busca")
    op.drop_table("modelos_ranking_busca")

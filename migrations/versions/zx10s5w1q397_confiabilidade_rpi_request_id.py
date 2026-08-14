"""confiabilidade da RPI e rastreabilidade por request id

Revision ID: zx10s5w1q397
Revises: zw99r4v0p286
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zx10s5w1q397"
down_revision: str | None = "zw99r4v0p286"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rpi_importacoes", sa.Column("arquivo_sha256", sa.String(64)))
    op.add_column("rpi_importacoes", sa.Column("arquivo_tamanho_bytes", sa.BigInteger()))
    op.add_column(
        "rpi_importacoes",
        sa.Column(
            "status_integridade", sa.String(20), nullable=False, server_default="desconhecido"
        ),
    )
    op.add_column(
        "rpi_importacoes",
        sa.Column("anomalias", sa.JSON(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_index(
        "ix_rpi_importacoes_status_integridade", "rpi_importacoes", ["status_integridade"]
    )

    for tabela in ("eventos_operacionais", "eventos_auditoria", "rpi_sync_execucoes"):
        op.add_column(tabela, sa.Column("request_id", sa.String(64)))
        op.create_index(f"ix_{tabela}_request_id", tabela, ["request_id"])
    op.add_column(
        "eventos_operacionais",
        sa.Column("detalhes", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    # Corrige a deriva já detectada pelo alembic check.
    op.create_index("ix_cadencias_nome", "cadencias", ["nome"])


def downgrade() -> None:
    op.drop_index("ix_cadencias_nome", table_name="cadencias")
    # Tolera ambientes que aplicaram uma versao preliminar desta migration.
    op.execute("ALTER TABLE eventos_operacionais DROP COLUMN IF EXISTS detalhes")
    for tabela in ("rpi_sync_execucoes", "eventos_auditoria", "eventos_operacionais"):
        op.drop_index(f"ix_{tabela}_request_id", table_name=tabela)
        op.drop_column(tabela, "request_id")
    op.drop_index("ix_rpi_importacoes_status_integridade", table_name="rpi_importacoes")
    for coluna in ("anomalias", "status_integridade", "arquivo_tamanho_bytes", "arquivo_sha256"):
        op.drop_column("rpi_importacoes", coluna)

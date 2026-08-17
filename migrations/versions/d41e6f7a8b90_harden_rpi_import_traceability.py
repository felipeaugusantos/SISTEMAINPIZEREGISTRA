"""harden RPI import traceability and monitored process priority"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d41e6f7a8b90"
down_revision: str | Sequence[str] | None = "c38d5e7a1b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rpi_importacoes", sa.Column("request_id", sa.String(64), nullable=True))
    op.add_column("rpi_importacoes", sa.Column("erro", sa.Text(), nullable=True))
    op.add_column(
        "rpi_importacoes",
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "rpi_importacoes",
        sa.Column(
            "fonte_oficial",
            sa.String(120),
            nullable=False,
            server_default="INPI - Revista da Propriedade Industrial",
        ),
    )
    op.create_index("ix_rpi_importacoes_request_id", "rpi_importacoes", ["request_id"])

    op.create_table(
        "rpi_importacoes_historico",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("numero_rpi", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(10), nullable=False),
        sa.Column("arquivo_sha256", sa.String(64), nullable=True),
        sa.Column("arquivo_tamanho_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("registros_processados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("titulares_processados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("classes_processadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("movimentacoes_processadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anomalias", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("numero_rpi", "tipo", "status", "request_id", "criado_em"):
        op.create_index(f"ix_rpi_importacoes_historico_{coluna}", "rpi_importacoes_historico", [coluna])

    op.add_column(
        "processos_monitorados",
        sa.Column("prioridade", sa.String(10), nullable=False, server_default="media"),
    )
    op.create_index("ix_processos_monitorados_prioridade", "processos_monitorados", ["prioridade"])


def downgrade() -> None:
    op.drop_index("ix_processos_monitorados_prioridade", table_name="processos_monitorados")
    op.drop_column("processos_monitorados", "prioridade")
    for coluna in ("criado_em", "request_id", "status", "tipo", "numero_rpi"):
        op.drop_index(f"ix_rpi_importacoes_historico_{coluna}", table_name="rpi_importacoes_historico")
    op.drop_table("rpi_importacoes_historico")
    op.drop_index("ix_rpi_importacoes_request_id", table_name="rpi_importacoes")
    for coluna in ("fonte_oficial", "tentativas", "erro", "request_id"):
        op.drop_column("rpi_importacoes", coluna)

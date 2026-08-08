"""add RPI synchronization monitoring

Revision ID: a61f37c9d2e4
Revises: f50b91a74e21
Create Date: 2026-08-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a61f37c9d2e4"
down_revision: str | Sequence[str] | None = "f50b91a74e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for coluna in (
        "titulares_processados",
        "classes_processadas",
        "movimentacoes_processadas",
    ):
        op.add_column(
            "rpi_importacoes",
            sa.Column(coluna, sa.Integer(), nullable=False, server_default="0"),
        )

    op.create_table(
        "rpi_sync_execucoes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("origem", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("solicitado_por", sa.String(length=150), nullable=True),
        sa.Column("execucao_anterior_id", sa.BigInteger(), nullable=True),
        sa.Column("rpi_inicio", sa.Integer(), nullable=True),
        sa.Column("rpi_fim", sa.Integer(), nullable=True),
        sa.Column("rpi_atual", sa.Integer(), nullable=True),
        sa.Column("ultima_rpi_oficial", sa.Integer(), nullable=True),
        sa.Column("ultima_rpi_local_antes", sa.Integer(), nullable=True),
        sa.Column("ultima_rpi_local_depois", sa.Integer(), nullable=True),
        sa.Column("edicoes_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("edicoes_processadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("registros_processados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("titulares_processados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("classes_processadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("movimentacoes_processadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mensagem", sa.Text(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "solicitado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["execucao_anterior_id"], ["rpi_sync_execucoes.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("origem", "status", "execucao_anterior_id", "solicitado_em", "finalizado_em"):
        op.create_index(f"ix_rpi_sync_execucoes_{coluna}", "rpi_sync_execucoes", [coluna])

    op.create_table(
        "rpi_sync_estado",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="iniciando"),
        sa.Column("execucao_atual_id", sa.BigInteger(), nullable=True),
        sa.Column("ultima_rpi_oficial", sa.Integer(), nullable=True),
        sa.Column("ultima_rpi_local", sa.Integer(), nullable=True),
        sa.Column("ultima_verificacao_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proxima_verificacao_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("falhas_consecutivas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["execucao_atual_id"], ["rpi_sync_execucoes.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rpi_sync_estado_status", "rpi_sync_estado", ["status"])
    op.create_index("ix_rpi_sync_estado_heartbeat_em", "rpi_sync_estado", ["heartbeat_em"])
    op.execute(
        "INSERT INTO rpi_sync_estado (id, status, falhas_consecutivas) "
        "VALUES (1, 'iniciando', 0) ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_index("ix_rpi_sync_estado_heartbeat_em", table_name="rpi_sync_estado")
    op.drop_index("ix_rpi_sync_estado_status", table_name="rpi_sync_estado")
    op.drop_table("rpi_sync_estado")
    for coluna in ("finalizado_em", "solicitado_em", "execucao_anterior_id", "status", "origem"):
        op.drop_index(f"ix_rpi_sync_execucoes_{coluna}", table_name="rpi_sync_execucoes")
    op.drop_table("rpi_sync_execucoes")
    for coluna in (
        "movimentacoes_processadas",
        "classes_processadas",
        "titulares_processados",
    ):
        op.drop_column("rpi_importacoes", coluna)

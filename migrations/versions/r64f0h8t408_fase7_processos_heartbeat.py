"""Fase 7: heartbeat de processos de fundo (painel tecnico)

Revision ID: r64f0h8t408
Revises: q53e9g7s397

processos_heartbeat: liveness do worker (consumidor de fila em loop, sem
endpoint HTTP proprio) para o painel tecnico de observabilidade e rollback.
Tabela global de operacao, sem RLS -- mesmo padrao de eventos_operacionais
e feature_flags_eventos (nao e dado de cliente).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r64f0h8t408"
down_revision: str | None = "q53e9g7s397"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processos_heartbeat",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("processo", sa.String(30), nullable=False, unique=True),
        sa.Column("heartbeat_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_processos_heartbeat_processo", "processos_heartbeat", ["processo"])
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "processos_heartbeat" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("processos_heartbeat")

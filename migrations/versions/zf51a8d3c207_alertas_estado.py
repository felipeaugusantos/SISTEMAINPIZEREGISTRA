"""estado dos alertas ativos do monitoramento (alertas_estado)

Revision ID: zf51a8d3c207
Revises: ze40a7c1d905
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zf51a8d3c207"
down_revision: str | None = "ze40a7c1d905"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABELA = "alertas_estado"


def upgrade() -> None:
    op.create_table(
        TABELA,
        sa.Column("fingerprint", sa.String(length=200), nullable=False),
        sa.Column("severidade", sa.String(length=20), nullable=False),
        sa.Column("titulo", sa.String(length=160), nullable=False),
        sa.Column("detalhe", sa.String(length=400), server_default="", nullable=False),
        sa.Column("desde_quando", sa.DateTime(timezone=True), nullable=False),
        sa.Column("em_falha", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("confirmado", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ausente_desde", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_aviso", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aviso_seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("aviso_tipo", sa.String(length=20), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("severidade IN ('critico', 'atencao')", name="ck_alertas_estado_severidade"),
        sa.PrimaryKeyConstraint("fingerprint"),
    )
    op.create_index("ix_alertas_estado_em_falha", TABELA, ["em_falha"])
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{TABELA}" TO inpi_app')


def downgrade() -> None:
    op.drop_index("ix_alertas_estado_em_falha", table_name=TABELA)
    op.drop_table(TABELA)

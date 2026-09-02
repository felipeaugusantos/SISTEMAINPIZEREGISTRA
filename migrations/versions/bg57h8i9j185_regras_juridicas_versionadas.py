"""regras juridicas versionadas: historico de parametros com vigencia

Revision ID: bg57h8i9j185
Revises: af46g7h8i074

Fase 3 da auditoria do modulo juridico (02/09/2026), achado 5.5: os
parametros juridicos que mudam por norma do INPI (data-marco de isencao da
taxa de concessao, prazo administrativo padrao em dias) ficam hoje fixos em
app/api/juridico.py, sem registro de quando cada valor passou a valer nem da
fonte legal que o justifica. Esta migration so cria a tabela de historico
(vazia); enquanto nao houver linha aplicavel para um codigo, o valor padrao
hardcoded no codigo continua valendo -- nenhum comportamento muda com este
deploy. Tabela de referencia global (nao e dado por organizacao): sem RLS,
mesmo padrao de afinidades_classes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bg57h8i9j185"
down_revision: str | None = "af46g7h8i074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "regras_juridicas_versionadas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("codigo", sa.String(80), nullable=False),
        sa.Column("valor", sa.JSON(), nullable=False),
        sa.Column("vigencia_inicio", sa.Date(), nullable=False),
        sa.Column("vigencia_fim", sa.Date(), nullable=True),
        sa.Column("fonte_legal", sa.Text(), nullable=False),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("criado_por", sa.String(254), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_regras_juridicas_versionadas_codigo", "regras_juridicas_versionadas", ["codigo"])


def downgrade() -> None:
    op.drop_table("regras_juridicas_versionadas")

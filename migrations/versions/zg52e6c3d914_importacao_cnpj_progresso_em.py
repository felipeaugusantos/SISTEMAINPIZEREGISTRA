"""Adiciona progresso_em às importações do cache nacional (CNPJ/RFB).

Achado 4 da auditoria de filas (07/10/2026): a recuperação automática só
considerava abandonada a execução sem nenhuma etapa. Com o carimbo do último
progresso, dá para detectar também a execução que registrou etapa mas parou
de avançar (o worker que a executava morreu).

Revision ID: zg52e6c3d914
Revises: zf41d5b2c813
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zg52e6c3d914"
down_revision: str | None = "zf41d5b2c813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("importacoes_cnpj_rfb", sa.Column("progresso_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("importacoes_cnpj_rfb", sa.Column("worker_token", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("importacoes_cnpj_rfb", "worker_token")
    op.drop_column("importacoes_cnpj_rfb", "progresso_em")

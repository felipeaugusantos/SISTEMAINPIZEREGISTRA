"""remove coluna orfa propostas_comerciais.atualizado_em (achado FASE6-2)

Revision ID: wo32p0r4c953
Revises: wn21o9q3b842

Achado FASE6-2 da auditoria (04/09/2026, alembic check): propostas_comerciais
tem a coluna atualizado_em no banco de produção, mas PropostaComercial
(app/models.py) não a declara -- nenhum código lê ou escreve nela (grep
confirmado). A tabela já rastreia mudanças de estado por campos específicos
com timestamp próprio (enviado_em, aceito_em, pagamento_confirmado_em,
protocolo_em etc), então um atualizado_em genérico nunca chegou a ser
reintroduzido. Downgrade recria a coluna vazia -- era uma coluna morta, sem
dado real para restaurar.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "wo32p0r4c953"
down_revision: str | None = "wn21o9q3b842"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("propostas_comerciais", "atualizado_em")


def downgrade() -> None:
    op.add_column(
        "propostas_comerciais",
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

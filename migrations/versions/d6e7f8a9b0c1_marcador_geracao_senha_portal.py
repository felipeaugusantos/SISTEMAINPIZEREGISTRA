"""marcador de geração de senha pra invalidar sessões do portal na troca

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0

Achado P1 do Codex no PR #122 (Fase 13.3 da auditoria fina do Portal do
Cliente, 23/09/2026): revogar sessões ativas (marcar revogada_em) ao
trocar a senha do cliente é uma corrida -- um login concorrente com a
senha antiga pode validar antes da troca e só criar/comitar a sessão
depois do SELECT de revogação já ter tirado o retrato, escapando da
revogação.

clientes_portal.senha_alterada_em vira um marcador de geração: toda
sessoes_clientes_portal.senha_versao_no_login guarda o valor vigente no
momento em que aquele login validou a senha. app.api.portal_cliente.
obter_cliente_portal passa a invalidar qualquer sessão cujo marcador não
bate com o valor atual do cliente -- a corrida se resolve sozinha na
próxima requisição autenticada, independente de qual transação comitou
primeiro (o SELECT-e-revogar explícito continua existindo como registro
de auditoria, não é mais o que garante a invalidação).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("clientes_portal", sa.Column("senha_alterada_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "sessoes_clientes_portal", sa.Column("senha_versao_no_login", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sessoes_clientes_portal", "senha_versao_no_login")
    op.drop_column("clientes_portal", "senha_alterada_em")

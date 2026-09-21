"""seguranca do portal do cliente: bloqueio de conta e csrf

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6

Achados alto e médio da auditoria do Portal do Cliente (Fase 9,
21/09/2026):

1. O login do portal só tinha rate-limit por IP, sem bloqueio da própria
   conta (diferente do login administrativo) -- um atacante rotacionando
   IPs podia tentar senha contra um cliente indefinidamente. Mesmos
   campos de UsuarioOperacoes (`tentativas_falhas`/`bloqueado_ate`) agora
   em `clientes_portal`.
2. Nenhuma mutação do portal exigia CSRF, diferente do painel
   administrativo. Nova coluna `csrf_hash` em `sessoes_clientes_portal`
   (nullable -- sessões existentes ficam sem o par e são tratadas como
   CSRF inválido em `exigir_csrf_portal`, forçando um novo login).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clientes_portal",
        sa.Column("tentativas_falhas", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("clientes_portal", sa.Column("bloqueado_ate", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sessoes_clientes_portal", sa.Column("csrf_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("sessoes_clientes_portal", "csrf_hash")
    op.drop_column("clientes_portal", "bloqueado_ate")
    op.drop_column("clientes_portal", "tentativas_falhas")

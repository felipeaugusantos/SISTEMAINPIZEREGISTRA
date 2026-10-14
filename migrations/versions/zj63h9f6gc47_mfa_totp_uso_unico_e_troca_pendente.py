"""MFA: TOTP de uso único e troca de autenticador com segredo pendente.

Auditoria externa de 14/10/2026:
- S04: ``mfa_ultimo_passo`` guarda o último passo de 30 s de TOTP aceito; um código só vale uma vez.
- S03: ``mfa_segredo_pendente`` e ``mfa_pendente_expira_em`` guardam o segredo novo durante a troca do
  autenticador. O fator ativo continua valendo até o novo ser confirmado.

Colunas anuláveis e sem valor padrão: contas existentes seguem iguais, ninguém é afetado no deploy.

Revision ID: zj63h9f6gc47
Revises: zi54g8e5fb36
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zj63h9f6gc47"
down_revision: str | None = "zi54g8e5fb36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usuarios_operacoes", sa.Column("mfa_ultimo_passo", sa.BigInteger(), nullable=True))
    op.add_column("usuarios_operacoes", sa.Column("mfa_segredo_pendente", sa.Text(), nullable=True))
    op.add_column(
        "usuarios_operacoes", sa.Column("mfa_pendente_expira_em", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("usuarios_operacoes", "mfa_pendente_expira_em")
    op.drop_column("usuarios_operacoes", "mfa_segredo_pendente")
    op.drop_column("usuarios_operacoes", "mfa_ultimo_passo")

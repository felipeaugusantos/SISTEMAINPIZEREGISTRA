"""Versão da senha nas contas e nas sessões administrativas.

Auditoria externa A04: uma sessão administrativa não registrava com qual geração da senha foi
autenticada. Um login que validou a senha antiga e gravou a sessão DEPOIS de uma redefinição de senha
(corrida login x recuperação) deixava um acesso utilizável com a senha que acabou de ser trocada.
``usuarios_operacoes.senha_versao`` sobe a cada troca/redefinição; ``sessoes_operacoes.senha_versao``
guarda a geração lida junto com a senha verificada, e a sessão deixa de valer se divergir.

Colunas novas com valor padrão 1 em ambas as tabelas: contas e sessões já existentes continuam
iguais (1 == 1), ninguém é desconectado no deploy. O portal do cliente já tem a proteção
equivalente (senha_alterada_em / senha_versao_no_login).

Revision ID: zi54g8e5fb36
Revises: zg52e6c3d914
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zi54g8e5fb36"
down_revision: str | None = "zg52e6c3d914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usuarios_operacoes", sa.Column("senha_versao", sa.Integer(), server_default=sa.text("1"), nullable=False)
    )
    op.add_column(
        "sessoes_operacoes", sa.Column("senha_versao", sa.Integer(), server_default=sa.text("1"), nullable=False)
    )


def downgrade() -> None:
    op.drop_column("sessoes_operacoes", "senha_versao")
    op.drop_column("usuarios_operacoes", "senha_versao")

"""add operations users and sessions

Revision ID: d14b8e6f3a22
Revises: c93a7d5e2f11
Create Date: 2026-08-06 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d14b8e6f3a22"
down_revision: str | Sequence[str] | None = "c93a7d5e2f11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "permissoes_operacoes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chave", sa.String(80), nullable=False),
        sa.Column("modulo", sa.String(50), nullable=False),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_permissoes_operacoes_chave", "permissoes_operacoes", ["chave"], unique=True)
    op.create_index("ix_permissoes_operacoes_modulo", "permissoes_operacoes", ["modulo"])
    op.create_table(
        "usuarios_operacoes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("nome", sa.String(150), nullable=False),
        sa.Column("usuario", sa.String(80), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("cargo", sa.String(150), nullable=True),
        sa.Column("perfil", sa.String(30), nullable=False, server_default="operador"),
        sa.Column("senha_hash", sa.Text(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("alterar_senha", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tentativas_falhas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bloqueado_ate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_login_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_por", sa.String(150), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usuarios_operacoes_nome", "usuarios_operacoes", ["nome"])
    op.create_index("ix_usuarios_operacoes_usuario", "usuarios_operacoes", ["usuario"], unique=True)
    op.create_index("ix_usuarios_operacoes_email", "usuarios_operacoes", ["email"], unique=True)
    op.create_index("ix_usuarios_operacoes_perfil", "usuarios_operacoes", ["perfil"])
    op.create_index("ix_usuarios_operacoes_ativo", "usuarios_operacoes", ["ativo"])
    op.create_index("ix_usuarios_operacoes_bloqueado_ate", "usuarios_operacoes", ["bloqueado_ate"])
    op.create_table(
        "usuario_permissoes",
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("permissao_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios_operacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permissao_id"], ["permissoes_operacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("usuario_id", "permissao_id"),
    )
    op.create_table(
        "sessoes_operacoes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ultimo_acesso_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revogada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motivo_revogacao", sa.String(150), nullable=True),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios_operacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessoes_operacoes_usuario_id", "sessoes_operacoes", ["usuario_id"])
    op.create_index("ix_sessoes_operacoes_token_hash", "sessoes_operacoes", ["token_hash"], unique=True)
    op.create_index("ix_sessoes_operacoes_ultimo_acesso_em", "sessoes_operacoes", ["ultimo_acesso_em"])
    op.create_index("ix_sessoes_operacoes_expira_em", "sessoes_operacoes", ["expira_em"])
    op.create_index("ix_sessoes_operacoes_revogada_em", "sessoes_operacoes", ["revogada_em"])


def downgrade() -> None:
    op.drop_table("sessoes_operacoes")
    op.drop_table("usuario_permissoes")
    op.drop_table("usuarios_operacoes")
    op.drop_table("permissoes_operacoes")

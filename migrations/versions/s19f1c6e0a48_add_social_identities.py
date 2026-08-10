"""add social login identities and one-time oauth attempts

Revision ID: s19f1c6e0a48
Revises: r18e0b5d9f37
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s19f1c6e0a48"
down_revision: str | None = "r18e0b5d9f37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"
BOOTSTRAP = "NULLIF(current_setting('app.organizacao_id', true), '') IS NULL"


def upgrade() -> None:
    op.create_table(
        "identidades_externas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("provedor", sa.String(length=20), nullable=False),
        sa.Column("provedor_usuario_id", sa.String(length=255), nullable=False),
        sa.Column("email_recebido", sa.String(length=254), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ultimo_login_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios_operacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provedor", "provedor_usuario_id", name="uq_identidade_provedor_subject"
        ),
        sa.UniqueConstraint("usuario_id", "provedor", name="uq_identidade_usuario_provedor"),
    )
    op.create_index(
        "ix_identidades_externas_organizacao_id", "identidades_externas", ["organizacao_id"]
    )
    op.create_index("ix_identidades_externas_usuario_id", "identidades_externas", ["usuario_id"])
    op.create_index("ix_identidades_externas_provedor", "identidades_externas", ["provedor"])
    op.create_table(
        "tentativas_oauth",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provedor", sa.String(length=20), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("browser_token_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=180), nullable=False),
        sa.Column("modo", sa.String(length=20), server_default="login", nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=True),
        sa.Column("destino", sa.String(length=300), server_default="/admin", nullable=False),
        sa.Column("mfa_token_hash", sa.String(length=64), nullable=True),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios_operacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("provedor", "state_hash", "usuario_id", "mfa_token_hash", "expira_em"):
        op.create_index(
            f"ix_tentativas_oauth_{coluna}", "tentativas_oauth", [coluna],
            unique=coluna in {"state_hash", "mfa_token_hash"},
        )
    op.execute('ALTER TABLE "identidades_externas" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "identidades_externas" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_bootstrap_read ON "identidades_externas" FOR SELECT '
        f'USING ({BOOTSTRAP} OR {SUPER} OR organizacao_id = {TENANT})'
    )
    op.execute(
        'CREATE POLICY tenant_write ON "identidades_externas" FOR ALL '
        f'USING ({SUPER} OR organizacao_id = {TENANT}) '
        f'WITH CHECK ({SUPER} OR organizacao_id = {TENANT})'
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS tenant_write ON "identidades_externas"')
    op.execute('DROP POLICY IF EXISTS tenant_bootstrap_read ON "identidades_externas"')
    op.drop_table("tentativas_oauth")
    op.drop_table("identidades_externas")

"""add local saas reliability controls

Revision ID: g47e9c3b8d22
Revises: f36d8b2a7c11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g47e9c3b8d22"
down_revision: str | None = "f36d8b2a7c11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizacoes",
        sa.Column(
            "suspender_automaticamente", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "organizacoes",
        sa.Column("retencao_dados_dias", sa.Integer(), nullable=False, server_default="730"),
    )
    op.add_column(
        "organizacoes",
        sa.Column(
            "politica_privacidade_versao", sa.String(30), nullable=False, server_default="1.0"
        ),
    )
    op.add_column(
        "dominios_organizacao", sa.Column("codigo_verificacao", sa.String(100), nullable=True)
    )
    op.add_column(
        "usuarios_operacoes",
        sa.Column("mfa_ativo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("usuarios_operacoes", sa.Column("mfa_segredo", sa.Text(), nullable=True))
    op.add_column(
        "usuarios_operacoes",
        sa.Column("codigos_recuperacao", sa.JSON(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "tokens_recuperacao_senha",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "usuario_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_em", sa.DateTime(timezone=True)),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_tokens_recuperacao_senha_usuario_id", "tokens_recuperacao_senha", ["usuario_id"]
    )
    op.create_index(
        "ix_tokens_recuperacao_senha_token_hash", "tokens_recuperacao_senha", ["token_hash"]
    )
    op.create_index(
        "ix_tokens_recuperacao_senha_expira_em", "tokens_recuperacao_senha", ["expira_em"]
    )

    op.create_table(
        "eventos_cobranca_sandbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("referencia", sa.String(100), nullable=False, unique=True),
        sa.Column("detalhes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_eventos_cobranca_sandbox_organizacao_id", "eventos_cobranca_sandbox", ["organizacao_id"]
    )
    op.create_index("ix_eventos_cobranca_sandbox_tipo", "eventos_cobranca_sandbox", ["tipo"])
    op.create_index("ix_eventos_cobranca_sandbox_status", "eventos_cobranca_sandbox", ["status"])
    op.create_index(
        "ix_eventos_cobranca_sandbox_referencia", "eventos_cobranca_sandbox", ["referencia"]
    )

    op.create_table(
        "alertas_sistema",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "organizacao_id", sa.BigInteger(), sa.ForeignKey("organizacoes.id", ondelete="CASCADE")
        ),
        sa.Column("severidade", sa.String(20), nullable=False),
        sa.Column("codigo", sa.String(60), nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column("detalhes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("resolvido_em", sa.DateTime(timezone=True)),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for column in ("organizacao_id", "severidade", "codigo"):
        op.create_index(f"ix_alertas_sistema_{column}", "alertas_sistema", [column])

    op.create_table(
        "solicitacoes_privacidade",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lead_id", sa.BigInteger(), sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="aberta"),
        sa.Column("solicitado_por", sa.String(254), nullable=False),
        sa.Column("concluido_por", sa.String(150)),
        sa.Column("detalhes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("concluido_em", sa.DateTime(timezone=True)),
    )
    for column in ("organizacao_id", "lead_id", "tipo", "status"):
        op.create_index(
            f"ix_solicitacoes_privacidade_{column}", "solicitacoes_privacidade", [column]
        )

    for table in ("eventos_cobranca_sandbox", "alertas_sistema", "solicitacoes_privacidade"):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        org_expr = (
            "organizacao_id = nullif(current_setting('app.organizacao_id', true), '')::bigint"
        )
        if table == "alertas_sistema":
            org_expr = f"(organizacao_id IS NULL OR {org_expr})"
        superadmin = "current_setting('app.superadmin', true) = 'true'"
        op.execute(
            f'CREATE POLICY {table}_tenant ON "{table}" '
            f"USING ({org_expr} OR {superadmin}) "
            f"WITH CHECK ({org_expr} OR {superadmin})"
        )
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO inpi_app')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "tokens_recuperacao_senha" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    for table in (
        "solicitacoes_privacidade",
        "alertas_sistema",
        "eventos_cobranca_sandbox",
        "tokens_recuperacao_senha",
    ):
        op.drop_table(table)
    op.drop_column("usuarios_operacoes", "codigos_recuperacao")
    op.drop_column("usuarios_operacoes", "mfa_segredo")
    op.drop_column("usuarios_operacoes", "mfa_ativo")
    op.drop_column("dominios_organizacao", "codigo_verificacao")
    op.drop_column("organizacoes", "politica_privacidade_versao")
    op.drop_column("organizacoes", "retencao_dados_dias")
    op.drop_column("organizacoes", "suspender_automaticamente")

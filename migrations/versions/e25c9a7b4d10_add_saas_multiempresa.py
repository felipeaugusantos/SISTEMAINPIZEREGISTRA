"""add SaaS organizations, plans and tenant isolation

Revision ID: e25c9a7b4d10
Revises: d14b8e6f3a22
Create Date: 2026-08-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e25c9a7b4d10"
down_revision: str | Sequence[str] | None = "d14b8e6f3a22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planos_saas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("nome", sa.String(100), nullable=False),
        sa.Column("codigo", sa.String(50), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("modulos", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("limites", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planos_saas_nome", "planos_saas", ["nome"], unique=True)
    op.create_index("ix_planos_saas_codigo", "planos_saas", ["codigo"], unique=True)
    op.create_index("ix_planos_saas_ativo", "planos_saas", ["ativo"])
    op.create_table(
        "organizacoes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("nome", sa.String(180), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("documento", sa.String(30), nullable=True),
        sa.Column("plano_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="ativa"),
        sa.Column("email_contato", sa.String(254), nullable=True),
        sa.Column("telefone_contato", sa.String(30), nullable=True),
        sa.Column("branding", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("assinatura_status", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("billing_provider", sa.String(30), nullable=True),
        sa.Column("billing_customer_id", sa.String(150), nullable=True),
        sa.Column("trial_ate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["plano_id"], ["planos_saas.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("documento"),
    )
    op.create_index("ix_organizacoes_nome", "organizacoes", ["nome"])
    op.create_index("ix_organizacoes_slug", "organizacoes", ["slug"], unique=True)
    op.create_index("ix_organizacoes_plano_id", "organizacoes", ["plano_id"])
    op.create_index("ix_organizacoes_status", "organizacoes", ["status"])
    op.create_index("ix_organizacoes_assinatura_status", "organizacoes", ["assinatura_status"])
    op.create_index("ix_organizacoes_billing_customer_id", "organizacoes", ["billing_customer_id"])
    op.create_table(
        "dominios_organizacao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("dominio", sa.String(255), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verificado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dominios_organizacao_organizacao_id", "dominios_organizacao", ["organizacao_id"])
    op.create_index("ix_dominios_organizacao_dominio", "dominios_organizacao", ["dominio"], unique=True)
    op.create_index("ix_dominios_organizacao_ativo", "dominios_organizacao", ["ativo"])
    op.create_table(
        "credenciais_integracao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("token_prefixo", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ultimo_uso_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_por", sa.String(150), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_credenciais_integracao_organizacao_id", "credenciais_integracao", ["organizacao_id"])
    op.create_index("ix_credenciais_integracao_token_prefixo", "credenciais_integracao", ["token_prefixo"])
    op.create_index("ix_credenciais_integracao_token_hash", "credenciais_integracao", ["token_hash"], unique=True)
    op.create_index("ix_credenciais_integracao_ativo", "credenciais_integracao", ["ativo"])
    op.create_table(
        "convites_organizacao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("perfil", sa.String(30), nullable=False, server_default="operador"),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("permissoes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aceito_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_por", sa.String(150), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_convites_organizacao_organizacao_id", "convites_organizacao", ["organizacao_id"])
    op.create_index("ix_convites_organizacao_email", "convites_organizacao", ["email"])
    op.create_index("ix_convites_organizacao_token_hash", "convites_organizacao", ["token_hash"], unique=True)
    op.create_index("ix_convites_organizacao_expira_em", "convites_organizacao", ["expira_em"])

    op.execute("INSERT INTO planos_saas (nome, codigo, descricao, modulos, limites) VALUES ('Profissional', 'profissional', 'Plano inicial migrado automaticamente', '[\"consulta\", \"leads\", \"validacao\", \"risco\", \"ia\", \"aprendizado\", \"usuarios\", \"rpi\", \"producao\"]', '{\"usuarios\": 50, \"pesquisas_mes\": 10000}')")
    op.execute("INSERT INTO organizacoes (nome, slug, plano_id, status, assinatura_status, branding) SELECT 'Zé Registra', 'ze-registra', id, 'ativa', 'manual', '{}' FROM planos_saas WHERE codigo = 'profissional'")

    for tabela in ("usuarios_operacoes", "leads", "pesquisas_marca"):
        op.add_column(tabela, sa.Column("organizacao_id", sa.BigInteger(), nullable=True))
        op.execute(f"UPDATE {tabela} SET organizacao_id = (SELECT id FROM organizacoes WHERE slug = 'ze-registra')")
        op.alter_column(tabela, "organizacao_id", nullable=False)
        op.create_foreign_key(f"fk_{tabela}_organizacao", tabela, "organizacoes", ["organizacao_id"], ["id"], ondelete="RESTRICT")
        op.create_index(f"ix_{tabela}_organizacao_id", tabela, ["organizacao_id"])
    op.add_column("usuarios_operacoes", sa.Column("superadmin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_usuarios_operacoes_superadmin", "usuarios_operacoes", ["superadmin"])
    op.execute("UPDATE usuarios_operacoes SET superadmin = true WHERE perfil = 'administrador' AND id = (SELECT min(id) FROM usuarios_operacoes WHERE perfil = 'administrador')")
    op.add_column("eventos_auditoria", sa.Column("organizacao_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_eventos_auditoria_organizacao", "eventos_auditoria", "organizacoes", ["organizacao_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_eventos_auditoria_organizacao_id", "eventos_auditoria", ["organizacao_id"])
    op.execute("UPDATE eventos_auditoria SET organizacao_id = (SELECT id FROM organizacoes WHERE slug = 'ze-registra')")


def downgrade() -> None:
    op.drop_index("ix_eventos_auditoria_organizacao_id", table_name="eventos_auditoria")
    op.drop_constraint("fk_eventos_auditoria_organizacao", "eventos_auditoria", type_="foreignkey")
    op.drop_column("eventos_auditoria", "organizacao_id")
    op.drop_index("ix_usuarios_operacoes_superadmin", table_name="usuarios_operacoes")
    op.drop_column("usuarios_operacoes", "superadmin")
    for tabela in ("pesquisas_marca", "leads", "usuarios_operacoes"):
        op.drop_index(f"ix_{tabela}_organizacao_id", table_name=tabela)
        op.drop_constraint(f"fk_{tabela}_organizacao", tabela, type_="foreignkey")
        op.drop_column(tabela, "organizacao_id")
    op.drop_table("convites_organizacao")
    op.drop_table("credenciais_integracao")
    op.drop_table("dominios_organizacao")
    op.drop_table("organizacoes")
    op.drop_table("planos_saas")

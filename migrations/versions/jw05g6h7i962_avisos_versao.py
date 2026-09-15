"""avisos de versao: notificacoes de atualizacao + confirmacao de leitura (Fase 3)

Revision ID: jw05g6h7i962
Revises: v19m4n2z742

Cria avisos_versao (aviso de plataforma, organizacao_id nulo = visivel a
todas as organizacoes, mesmo padrao de alertas_sistema) e
avisos_versao_confirmacoes (confirmacao individual de leitura: usuario,
organizacao, data, ip hasheado -- mesmo padrao de
assinaturas_propostas_comerciais).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "jw05g6h7i962"
down_revision: str | None = "v19m4n2z742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "avisos_versao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=True),
        sa.Column("versao", sa.String(length=30), nullable=False),
        sa.Column("titulo", sa.String(length=200), nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column("severidade", sa.String(length=20), nullable=False),
        sa.Column("critico", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("criado_por_id", sa.BigInteger(), nullable=True),
        sa.Column("publicado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_avisos_versao_organizacao_id", "avisos_versao", ["organizacao_id"])
    op.create_index("ix_avisos_versao_severidade", "avisos_versao", ["severidade"])
    op.create_index("ix_avisos_versao_critico", "avisos_versao", ["critico"])
    op.create_index("ix_avisos_versao_ativo", "avisos_versao", ["ativo"])
    op.create_index("ix_avisos_versao_publicado_em", "avisos_versao", ["publicado_em"])

    op.create_table(
        "avisos_versao_confirmacoes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("aviso_id", sa.BigInteger(), nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("confirmado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["aviso_id"], ["avisos_versao.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios_operacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("aviso_id", "usuario_id", name="uq_aviso_versao_confirmacao"),
    )
    op.create_index("ix_avisos_versao_confirmacoes_aviso_id", "avisos_versao_confirmacoes", ["aviso_id"])
    op.create_index(
        "ix_avisos_versao_confirmacoes_organizacao_id", "avisos_versao_confirmacoes", ["organizacao_id"]
    )
    op.create_index("ix_avisos_versao_confirmacoes_usuario_id", "avisos_versao_confirmacoes", ["usuario_id"])
    op.create_index("ix_avisos_versao_confirmacoes_confirmado_em", "avisos_versao_confirmacoes", ["confirmado_em"])

    op.execute('ALTER TABLE "avisos_versao" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "avisos_versao" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "avisos_versao" '
        f"USING ({SUPER} OR organizacao_id IS NULL OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id IS NULL OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "avisos_versao" TO inpi_app')

    op.execute('ALTER TABLE "avisos_versao_confirmacoes" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "avisos_versao_confirmacoes" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "avisos_versao_confirmacoes" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "avisos_versao_confirmacoes" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_index("ix_avisos_versao_confirmacoes_confirmado_em", table_name="avisos_versao_confirmacoes")
    op.drop_index("ix_avisos_versao_confirmacoes_usuario_id", table_name="avisos_versao_confirmacoes")
    op.drop_index("ix_avisos_versao_confirmacoes_organizacao_id", table_name="avisos_versao_confirmacoes")
    op.drop_index("ix_avisos_versao_confirmacoes_aviso_id", table_name="avisos_versao_confirmacoes")
    op.drop_table("avisos_versao_confirmacoes")

    op.drop_index("ix_avisos_versao_publicado_em", table_name="avisos_versao")
    op.drop_index("ix_avisos_versao_ativo", table_name="avisos_versao")
    op.drop_index("ix_avisos_versao_critico", table_name="avisos_versao")
    op.drop_index("ix_avisos_versao_severidade", table_name="avisos_versao")
    op.drop_index("ix_avisos_versao_organizacao_id", table_name="avisos_versao")
    op.drop_table("avisos_versao")

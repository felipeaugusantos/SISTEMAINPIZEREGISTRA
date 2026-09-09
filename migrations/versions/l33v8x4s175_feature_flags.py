"""Fase 4: feature flags com ativacao controlada por organizacao

Revision ID: l33v8x4s175
Revises: k22u7w3r064

feature_flags: cadastro global da flag (codigo, nome, descricao, modulos
envolvidos, dependencias, estado padrao, responsavel, kill-switch geral,
data de ativacao/expiracao). Leitura liberada, escrita restrita a
superadmin -- mesmo padrao de versoes_sistema (Fase 1).

feature_flags_organizacoes: autorizacao granular por organizacao (achado
do criterio de aceite: uma organizacao testa sem afetar as demais). Sem
registro para o par (flag, organizacao), a organizacao usa
feature_flags.estado_padrao (ver app.feature_flags.flag_ativa).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l33v8x4s175"
down_revision: str | None = "k22u7w3r064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "organizacao_id = nullif(current_setting('app.organizacao_id', true), '')::bigint"
SUPERADMIN = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("codigo", sa.String(80), nullable=False, unique=True),
        sa.Column("nome", sa.String(180), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("modulos_envolvidos", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("dependencias", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("estado_padrao", sa.String(30), nullable=False, server_default="desligado"),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "responsavel_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"),
        ),
        sa.Column("data_ativacao", sa.DateTime(timezone=True)),
        sa.Column("data_expiracao", sa.DateTime(timezone=True)),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "estado_padrao IN ('desligado', 'somente_administradores', 'ligado')",
            name="ck_feature_flag_estado_padrao",
        ),
    )
    op.create_index("ix_feature_flags_codigo", "feature_flags", ["codigo"])

    op.create_table(
        "feature_flags_organizacoes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "feature_flag_id",
            sa.BigInteger(),
            sa.ForeignKey("feature_flags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("adiado_ate", sa.DateTime(timezone=True)),
        sa.Column(
            "responsavel_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"),
        ),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("feature_flag_id", "organizacao_id", name="uq_feature_flag_organizacao"),
        sa.CheckConstraint(
            "estado IN ('ativo', 'somente_administradores', 'adiado', 'desativado')",
            name="ck_feature_flag_org_estado",
        ),
    )
    for coluna in ("feature_flag_id", "organizacao_id"):
        op.create_index(f"ix_feature_flags_organizacoes_{coluna}", "feature_flags_organizacoes", [coluna])

    op.execute('ALTER TABLE "feature_flags" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "feature_flags" FORCE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY feature_flags_read ON "feature_flags" FOR SELECT USING (true)')
    op.execute(f'CREATE POLICY feature_flags_insert ON "feature_flags" FOR INSERT WITH CHECK ({SUPERADMIN})')
    op.execute(
        f'CREATE POLICY feature_flags_update ON "feature_flags" FOR UPDATE '
        f"USING ({SUPERADMIN}) WITH CHECK ({SUPERADMIN})"
    )
    op.execute(f'CREATE POLICY feature_flags_delete ON "feature_flags" FOR DELETE USING ({SUPERADMIN})')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "feature_flags" TO inpi_app')

    op.execute('ALTER TABLE "feature_flags_organizacoes" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "feature_flags_organizacoes" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY feature_flags_organizacoes_select ON "feature_flags_organizacoes" FOR SELECT '
        f"USING ({TENANT} OR {SUPERADMIN})"
    )
    op.execute(
        f'CREATE POLICY feature_flags_organizacoes_insert ON "feature_flags_organizacoes" FOR INSERT '
        f"WITH CHECK ({SUPERADMIN})"
    )
    op.execute(
        f'CREATE POLICY feature_flags_organizacoes_update ON "feature_flags_organizacoes" FOR UPDATE '
        f"USING ({SUPERADMIN}) WITH CHECK ({SUPERADMIN})"
    )
    op.execute(
        f'CREATE POLICY feature_flags_organizacoes_delete ON "feature_flags_organizacoes" FOR DELETE '
        f"USING ({SUPERADMIN})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "feature_flags_organizacoes" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("feature_flags_organizacoes")
    op.drop_table("feature_flags")

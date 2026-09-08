"""governanca de retencao e legal hold

Revision ID: h19r4t0n731
Revises: mg08b3i9u620
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h19r4t0n731"
down_revision: str | None = "mg08b3i9u620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _habilitar_rls(tabela: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    tenant = "organizacao_id = nullif(current_setting('app.organizacao_id', true), '')::bigint"
    superadmin = "current_setting('app.superadmin', true) = 'true'"
    op.execute(
        f'CREATE POLICY {tabela}_tenant ON "{tabela}" '
        f"USING ({tenant} OR {superadmin}) WITH CHECK ({tenant} OR {superadmin})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{tabela}" TO inpi_app')


def upgrade() -> None:
    op.create_table(
        "politicas_retencao",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("organizacao_id", sa.BigInteger(), sa.ForeignKey("organizacoes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("categoria", sa.String(30), nullable=False),
        sa.Column("prazo_dias", sa.Integer(), nullable=False),
        sa.Column("marco_inicial", sa.String(80), nullable=False),
        sa.Column("finalidade", sa.Text(), nullable=False),
        sa.Column("base_legal", sa.Text(), nullable=False),
        sa.Column("vigencia_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("justificativa", sa.Text(), nullable=False),
        sa.Column("excecoes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reducao", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reducao_confirmada", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("criado_por_id", sa.BigInteger(), sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL")),
        sa.Column("criado_por", sa.String(254), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("categoria IN ('lead')", name="ck_politica_retencao_categoria"),
        sa.CheckConstraint("prazo_dias BETWEEN 30 AND 3650", name="ck_politica_retencao_prazo"),
    )
    for coluna in ("organizacao_id", "categoria", "vigencia_em", "ativo", "criado_por_id", "criado_em"):
        op.create_index(f"ix_politicas_retencao_{coluna}", "politicas_retencao", [coluna])

    op.create_table(
        "simulacoes_retencao",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("organizacao_id", sa.BigInteger(), sa.ForeignKey("organizacoes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("categoria", sa.String(30), nullable=False),
        sa.Column("prazo_dias", sa.Integer(), nullable=False),
        sa.Column("resultado", sa.JSON(), nullable=False),
        sa.Column("criado_por_id", sa.BigInteger(), sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL")),
        sa.Column("criado_por", sa.String(254), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("usada_em", sa.DateTime(timezone=True)),
        sa.CheckConstraint("categoria IN ('lead')", name="ck_simulacao_retencao_categoria"),
        sa.CheckConstraint("prazo_dias BETWEEN 30 AND 3650", name="ck_simulacao_retencao_prazo"),
    )
    for coluna in ("organizacao_id", "categoria", "criado_por_id", "criado_em", "usada_em"):
        op.create_index(f"ix_simulacoes_retencao_{coluna}", "simulacoes_retencao", [coluna])

    op.create_table(
        "bloqueios_retencao",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("organizacao_id", sa.BigInteger(), sa.ForeignKey("organizacoes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("categoria", sa.String(30), nullable=False),
        sa.Column("recurso_id", sa.BigInteger(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("criado_por_id", sa.BigInteger(), sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL")),
        sa.Column("criado_por", sa.String(254), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("liberado_por_id", sa.BigInteger(), sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL")),
        sa.Column("liberado_por", sa.String(254)),
        sa.Column("liberado_em", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organizacao_id", "categoria", "recurso_id", name="uq_bloqueio_retencao_recurso"),
        sa.CheckConstraint("categoria IN ('lead')", name="ck_bloqueio_retencao_categoria"),
    )
    for coluna in ("organizacao_id", "categoria", "recurso_id", "ativo", "criado_em", "liberado_em"):
        op.create_index(f"ix_bloqueios_retencao_{coluna}", "bloqueios_retencao", [coluna])

    for tabela in ("politicas_retencao", "simulacoes_retencao", "bloqueios_retencao"):
        _habilitar_rls(tabela)
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("bloqueios_retencao")
    op.drop_table("simulacoes_retencao")
    op.drop_table("politicas_retencao")

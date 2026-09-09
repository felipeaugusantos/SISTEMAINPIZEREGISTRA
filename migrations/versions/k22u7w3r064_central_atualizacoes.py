"""central de atualizacoes e interacoes por usuario

Revision ID: k22u7w3r064
Revises: j21t6v2q953
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k22u7w3r064"
down_revision: str | None = "j21t6v2q953"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "organizacao_id = nullif(current_setting('app.organizacao_id', true), '')::bigint"
SUPERADMIN = "current_setting('app.superadmin', true) = 'true'"


def _rls_tenant(tabela: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY {tabela}_select ON "{tabela}" FOR SELECT USING ({TENANT} OR {SUPERADMIN})')
    op.execute(
        f'CREATE POLICY {tabela}_insert ON "{tabela}" FOR INSERT WITH CHECK ({TENANT} OR {SUPERADMIN})'
    )
    op.execute(
        f'CREATE POLICY {tabela}_update ON "{tabela}" FOR UPDATE '
        f'USING ({TENANT} OR {SUPERADMIN}) WITH CHECK ({TENANT} OR {SUPERADMIN})'
    )
    op.execute(f'CREATE POLICY {tabela}_delete ON "{tabela}" FOR DELETE USING ({TENANT} OR {SUPERADMIN})')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{tabela}" TO inpi_app')


def upgrade() -> None:
    op.add_column("versoes_sistema", sa.Column("impacto_usuario", sa.Text()))
    op.add_column("versoes_sistema", sa.Column("documentacao_url", sa.String(500)))
    op.add_column(
        "versoes_sistema",
        sa.Column("permite_adiar", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "interacoes_versoes_sistema",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "versao_sistema_id",
            sa.BigInteger(),
            sa.ForeignKey("versoes_sistema.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "usuario_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("confirmado_em", sa.DateTime(timezone=True)),
        sa.Column("adiado_ate", sa.DateTime(timezone=True)),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "versao_sistema_id", "organizacao_id", "usuario_id", name="uq_interacao_versao_usuario"
        ),
    )
    for coluna in ("versao_sistema_id", "organizacao_id", "usuario_id"):
        op.create_index(f"ix_interacoes_versoes_sistema_{coluna}", "interacoes_versoes_sistema", [coluna])

    op.create_table(
        "problemas_versoes_sistema",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "versao_sistema_id",
            sa.BigInteger(),
            sa.ForeignKey("versoes_sistema.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "usuario_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("categoria", sa.String(20), nullable=False),
        sa.Column("modulo", sa.String(60)),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="aberto"),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "categoria IN ('erro', 'duvida', 'regressao')", name="ck_problema_versao_categoria"
        ),
        sa.CheckConstraint(
            "status IN ('aberto', 'em_analise', 'resolvido')", name="ck_problema_versao_status"
        ),
    )
    for coluna in ("versao_sistema_id", "organizacao_id", "usuario_id", "status", "criado_em"):
        op.create_index(f"ix_problemas_versoes_sistema_{coluna}", "problemas_versoes_sistema", [coluna])

    _rls_tenant("interacoes_versoes_sistema")
    _rls_tenant("problemas_versoes_sistema")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("problemas_versoes_sistema")
    op.drop_table("interacoes_versoes_sistema")
    op.drop_column("versoes_sistema", "permite_adiar")
    op.drop_column("versoes_sistema", "documentacao_url")
    op.drop_column("versoes_sistema", "impacto_usuario")

"""portfolio completo de propriedade intelectual"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "aab14c7d5e61"
down_revision: str = "aaa13b6c4d50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(tabela: str) -> None:
    op.execute(f"ALTER TABLE {tabela} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {tabela}_tenant ON {tabela} USING "
        "(current_setting('app.superadmin', true) = 'true' OR "
        "organizacao_id = NULLIF(current_setting('app.organizacao_id', true), '')::bigint) "
        "WITH CHECK (current_setting('app.superadmin', true) = 'true' OR "
        "organizacao_id = NULLIF(current_setting('app.organizacao_id', true), '')::bigint)"
    )


def upgrade() -> None:
    op.create_table(
        "ativos_pi",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("cliente_portal_id", sa.BigInteger(), nullable=True),
        sa.Column("titular_id", sa.BigInteger(), nullable=False),
        sa.Column("codigo", sa.String(80), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("nome", sa.String(240), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="ativo"),
        sa.Column("vigencia_inicio", sa.Date(), nullable=True),
        sa.Column("vigencia_fim", sa.Date(), nullable=True),
        sa.Column("dados", sa.JSON(), nullable=False),
        sa.Column("criado_por", sa.String(254), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cliente_portal_id"], ["clientes_portal.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["titular_id"], ["titulares.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "codigo", name="uq_ativo_pi_org_codigo"),
    )
    op.create_index("ix_ativos_pi_organizacao_id", "ativos_pi", ["organizacao_id"])
    op.create_index("ix_ativos_pi_cliente_portal_id", "ativos_pi", ["cliente_portal_id"])
    op.create_index("ix_ativos_pi_titular_id", "ativos_pi", ["titular_id"])
    op.create_index("ix_ativos_pi_codigo", "ativos_pi", ["codigo"])
    op.create_index("ix_ativos_pi_tipo", "ativos_pi", ["tipo"])
    op.create_index("ix_ativos_pi_nome", "ativos_pi", ["nome"])
    op.create_index("ix_ativos_pi_status", "ativos_pi", ["status"])
    op.create_index("ix_ativos_pi_vigencia_fim", "ativos_pi", ["vigencia_fim"])

    op.create_table(
        "ativos_processos_pi",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("ativo_id", sa.BigInteger(), nullable=False),
        sa.Column("processo_id", sa.BigInteger(), nullable=False),
        sa.Column("papel", sa.String(30), nullable=False, server_default="principal"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ativo_id"], ["ativos_pi.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["processo_id"], ["processos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ativo_id", "processo_id", name="uq_ativo_pi_processo"),
    )
    op.create_index("ix_ativos_processos_pi_organizacao_id", "ativos_processos_pi", ["organizacao_id"])
    op.create_index("ix_ativos_processos_pi_ativo_id", "ativos_processos_pi", ["ativo_id"])
    op.create_index("ix_ativos_processos_pi_processo_id", "ativos_processos_pi", ["processo_id"])

    op.create_table(
        "ativos_partes_pi",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("ativo_id", sa.BigInteger(), nullable=False),
        sa.Column("papel", sa.String(20), nullable=False),
        sa.Column("nome", sa.String(240), nullable=False),
        sa.Column("documento", sa.String(30), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ativo_id"], ["ativos_pi.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ativo_id", "papel", "nome", name="uq_ativo_pi_parte"),
    )
    op.create_index("ix_ativos_partes_pi_organizacao_id", "ativos_partes_pi", ["organizacao_id"])
    op.create_index("ix_ativos_partes_pi_ativo_id", "ativos_partes_pi", ["ativo_id"])
    op.create_index("ix_ativos_partes_pi_papel", "ativos_partes_pi", ["papel"])

    op.create_table(
        "documentos_ativos_pi",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("ativo_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("hash_documento", sa.String(64), nullable=False),
        sa.Column("caminho", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("criado_por", sa.String(254), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ativo_id"], ["ativos_pi.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ativo_id", "versao", name="uq_documento_ativo_pi_versao"),
    )
    op.create_index("ix_documentos_ativos_pi_organizacao_id", "documentos_ativos_pi", ["organizacao_id"])
    op.create_index("ix_documentos_ativos_pi_ativo_id", "documentos_ativos_pi", ["ativo_id"])
    op.create_index("ix_documentos_ativos_pi_hash_documento", "documentos_ativos_pi", ["hash_documento"])
    for tabela in ("ativos_pi", "ativos_processos_pi", "ativos_partes_pi", "documentos_ativos_pi"):
        _rls(tabela)


def downgrade() -> None:
    for tabela in ("documentos_ativos_pi", "ativos_partes_pi", "ativos_processos_pi", "ativos_pi"):
        op.execute(f"DROP POLICY IF EXISTS {tabela}_tenant ON {tabela}")
        op.execute(f"ALTER TABLE {tabela} DISABLE ROW LEVEL SECURITY")
    op.drop_table("documentos_ativos_pi")
    op.drop_table("ativos_partes_pi")
    op.drop_table("ativos_processos_pi")
    op.drop_table("ativos_pi")

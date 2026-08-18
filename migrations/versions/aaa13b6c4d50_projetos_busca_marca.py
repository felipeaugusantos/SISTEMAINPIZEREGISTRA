"""projetos salvos para busca avançada de anterioridade"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "aaa13b6c4d50"
down_revision: tuple[str, str] = ("zz32u7y3s519", "zx01p0rtalfks")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projetos_busca_marca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(180), nullable=False),
        sa.Column("consulta", sa.JSON(), nullable=False),
        sa.Column("versao_busca", sa.String(60), nullable=False, server_default="busca-marcas-4.0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ativo"),
        sa.Column("ultima_execucao", sa.JSON(), nullable=True),
        sa.Column("executado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_por", sa.String(150), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "slug", name="uq_projeto_busca_org_slug"),
    )
    op.create_index("ix_projetos_busca_marca_organizacao_id", "projetos_busca_marca", ["organizacao_id"])
    op.create_index("ix_projetos_busca_marca_slug", "projetos_busca_marca", ["slug"])
    op.create_index("ix_projetos_busca_marca_status", "projetos_busca_marca", ["status"])
    op.execute("ALTER TABLE projetos_busca_marca ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY projetos_busca_marca_tenant ON projetos_busca_marca "
        "USING (current_setting('app.superadmin', true) = 'true' "
        "OR organizacao_id = NULLIF(current_setting('app.organizacao_id', true), '')::bigint) "
        "WITH CHECK (current_setting('app.superadmin', true) = 'true' "
        "OR organizacao_id = NULLIF(current_setting('app.organizacao_id', true), '')::bigint)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS projetos_busca_marca_tenant ON projetos_busca_marca")
    op.execute("ALTER TABLE projetos_busca_marca DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_projetos_busca_marca_status", table_name="projetos_busca_marca")
    op.drop_index("ix_projetos_busca_marca_slug", table_name="projetos_busca_marca")
    op.drop_index("ix_projetos_busca_marca_organizacao_id", table_name="projetos_busca_marca")
    op.drop_table("projetos_busca_marca")

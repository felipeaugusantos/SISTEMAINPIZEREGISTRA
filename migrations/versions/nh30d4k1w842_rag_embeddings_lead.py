"""RAG local (pgvector): embeddings de leads com resultado conhecido

Revision ID: nh30d4k1w842
Revises: mg08b3i9u620

Fase de "casos semelhantes" da IA em sombra (08/09/2026): antes de sugerir a
proxima acao de um lead, o modelo local passa a receber precedentes reais --
leads parecidos que ja tiveram um resultado conhecido (ganho/perdido). A
similaridade e calculada por embedding (extensao pgvector), gerado tambem
localmente via Ollama (mesmo servidor de app.ia_sombra, endpoint
/api/embeddings), sem nenhuma chamada a API paga. So indexa leads com
Lead.resultado preenchido -- sem resultado nao ha o que aprender.

Pre-requisito de infraestrutura: o Postgres de producao/teste passa a usar a
imagem pgvector/pgvector:pg16 (compose.yaml) em vez de postgres:16-alpine,
que nao tem a extensao disponivel.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "nh30d4k1w842"
down_revision: str | None = "mg08b3i9u620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"
DIMENSOES_EMBEDDING = 768  # nomic-embed-text


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "embeddings_lead",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("resumo_indexado", sa.Text(), nullable=False),
        sa.Column("resultado", sa.String(length=12), nullable=False),
        sa.Column("modelo", sa.String(length=120), nullable=False),
        sa.Column("embedding", Vector(DIMENSOES_EMBEDDING), nullable=False),
        sa.Column("gerado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", name="uq_embeddings_lead_lead_id"),
    )
    op.create_index("ix_embeddings_lead_organizacao_id", "embeddings_lead", ["organizacao_id"])
    op.create_index("ix_embeddings_lead_lead_id", "embeddings_lead", ["lead_id"])
    op.execute(
        "CREATE INDEX ix_embeddings_lead_embedding_cosine ON embeddings_lead "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute('ALTER TABLE "embeddings_lead" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "embeddings_lead" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "embeddings_lead" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "embeddings_lead" TO inpi_app')


def downgrade() -> None:
    op.drop_table("embeddings_lead")
    op.execute("DROP EXTENSION IF EXISTS vector")

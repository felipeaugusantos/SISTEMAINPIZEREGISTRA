"""ia em sombra: explicacao de risco em linguagem simples e qualificacao na captacao de leads

Revision ID: ic64x9e5q286
Revises: hz42u8b4n064

Extensao da IA em sombra (leads) para duas novas frentes, ambas nunca
decidem/substituem calculo deterministico, sempre exigem revisao humana:

- explicacoes_analise_marca: traduz pontuacao/nivel/principais_conflitos
  ja persistidos em AvaliacaoRiscoMarca para linguagem simples. Nao
  reaproveita a tabela residual explicacoes_risco_ia (fk'd 1:1 a
  AvaliacaoRiscoMarca, desenhada para um provedor pago com custo por
  chamada -- desenho diferente do padrao local/Ollama ja em producao).
- qualificacoes_ia_lead: prioridade + observacao sugeridas no momento da
  captacao do lead (formulario publico ou conversao do Radar de
  Prospeccao), gerada uma unica vez por lead.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ic64x9e5q286"
down_revision: str | None = "hz42u8b4n064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def _rls(tabela: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{tabela}" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{tabela}" TO inpi_app')


def upgrade() -> None:
    op.create_table(
        "explicacoes_analise_marca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("pesquisa_id", sa.String(length=36), nullable=False),
        sa.Column("avaliacao_risco_id", sa.BigInteger(), nullable=False),
        sa.Column("gerado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("modelo", sa.String(length=120), nullable=False),
        sa.Column("explicacao", sa.Text(), server_default="", nullable=False),
        sa.Column("baseado_em_calculado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pendente", nullable=False),
        sa.Column("revisado_por", sa.String(length=254), nullable=True),
        sa.Column("revisado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas_marca.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["avaliacao_risco_id"], ["avaliacoes_risco_marca.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_explicacoes_analise_marca_organizacao_id", "explicacoes_analise_marca", ["organizacao_id"])
    op.create_index("ix_explicacoes_analise_marca_pesquisa_id", "explicacoes_analise_marca", ["pesquisa_id"])
    op.create_index(
        "ix_explicacoes_analise_marca_avaliacao_risco_id", "explicacoes_analise_marca", ["avaliacao_risco_id"]
    )
    op.create_index("ix_explicacoes_analise_marca_gerado_em", "explicacoes_analise_marca", ["gerado_em"])
    op.create_index("ix_explicacoes_analise_marca_status", "explicacoes_analise_marca", ["status"])
    _rls("explicacoes_analise_marca")

    op.create_table(
        "qualificacoes_ia_lead",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("gerado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("modelo", sa.String(length=120), nullable=False),
        sa.Column("prioridade", sa.String(length=10), server_default="media", nullable=False),
        sa.Column("observacao", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pendente", nullable=False),
        sa.Column("revisado_por", sa.String(length=254), nullable=True),
        sa.Column("revisado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", name="uq_qualificacao_ia_lead"),
    )
    op.create_index("ix_qualificacoes_ia_lead_organizacao_id", "qualificacoes_ia_lead", ["organizacao_id"])
    op.create_index("ix_qualificacoes_ia_lead_lead_id", "qualificacoes_ia_lead", ["lead_id"])
    op.create_index("ix_qualificacoes_ia_lead_gerado_em", "qualificacoes_ia_lead", ["gerado_em"])
    op.create_index("ix_qualificacoes_ia_lead_prioridade", "qualificacoes_ia_lead", ["prioridade"])
    op.create_index("ix_qualificacoes_ia_lead_status", "qualificacoes_ia_lead", ["status"])
    _rls("qualificacoes_ia_lead")


def downgrade() -> None:
    op.drop_table("qualificacoes_ia_lead")
    op.drop_table("explicacoes_analise_marca")

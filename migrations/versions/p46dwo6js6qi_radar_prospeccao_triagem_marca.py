"""radar de prospeccao: triagem de marca (Fase 4)

Revision ID: p46dwo6js6qi
Revises: mwywsy5j3zk5

Fase 4 do roadmap do Radar de Prospeccao (03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md). Reaproveita o motor de
busca ja existente (app.search.buscar_marcas) -- nao duplica infraestrutura.

IMPORTANTE (exigencia explicita do usuario): a triagem automatica NUNCA pode
afirmar que uma marca esta disponivel para registro. O vocabulario de
classificacao e fechado por CHECK CONSTRAINT as 5 classificacoes abaixo --
nao existe "disponivel"/"livre" no banco, de proposito.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p46dwo6js6qi"
down_revision: str | None = "mwywsy5j3zk5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"

CLASSIFICACOES_TRIAGEM = (
    "nao_localizado",
    "resultado_semelhante",
    "resultado_relevante_localizado",
    "inconclusivo",
    "analise_humana_necessaria",
)


def upgrade() -> None:
    op.add_column("prospects", sa.Column("triagem_marca_status", sa.String(length=30), nullable=True))
    op.add_column("prospects", sa.Column("triagem_marca_em", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_prospects_triagem_marca_status", "prospects", ["triagem_marca_status"])
    op.create_check_constraint(
        "ck_prospects_triagem_marca_status_valido",
        "prospects",
        "triagem_marca_status IS NULL OR triagem_marca_status IN ("
        + ", ".join(f"'{v}'" for v in CLASSIFICACOES_TRIAGEM)
        + ")",
    )

    op.create_table(
        "prospect_triagens",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("prospect_id", sa.BigInteger(), nullable=False),
        sa.Column("marca_pesquisada", sa.String(length=200), nullable=False),
        sa.Column("classificacao", sa.String(length=30), nullable=False),
        sa.Column("justificativa", sa.Text(), nullable=False),
        sa.Column("total_resultados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prospect_id"], ["prospects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "classificacao IN (" + ", ".join(f"'{v}'" for v in CLASSIFICACOES_TRIAGEM) + ")",
            name="ck_prospect_triagens_classificacao_valida",
        ),
    )
    op.create_index("ix_prospect_triagens_organizacao_id", "prospect_triagens", ["organizacao_id"])
    op.create_index("ix_prospect_triagens_prospect_id", "prospect_triagens", ["prospect_id"])
    op.execute('ALTER TABLE "prospect_triagens" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "prospect_triagens" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "prospect_triagens" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "prospect_triagens" TO inpi_app')


def downgrade() -> None:
    op.drop_table("prospect_triagens")
    op.drop_constraint("ck_prospects_triagem_marca_status_valido", "prospects", type_="check")
    op.drop_index("ix_prospects_triagem_marca_status", table_name="prospects")
    op.drop_column("prospects", "triagem_marca_em")
    op.drop_column("prospects", "triagem_marca_status")

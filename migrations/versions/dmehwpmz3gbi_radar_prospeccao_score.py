"""radar de prospeccao: score comercial e aprovacao (Fase 5)

Revision ID: dmehwpmz3gbi
Revises: p46dwo6js6qi

Fase 5 do roadmap do Radar de Prospeccao (03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md). Adiciona "aprovado" ao
vocabulario de status de Prospect (existia so novo/rejeitado/duplicado/
convertido_lead ate aqui) e uma politica por organizacao para aprovacao
automatica por threshold de score (opt-in, desligada por padrao).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "dmehwpmz3gbi"
down_revision: str | None = "p46dwo6js6qi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"

STATUS_PROSPECT_ANTIGOS = ("novo", "rejeitado", "duplicado", "convertido_lead")
STATUS_PROSPECT_NOVOS = (*STATUS_PROSPECT_ANTIGOS, "aprovado")


def upgrade() -> None:
    op.add_column("prospects", sa.Column("score", sa.Float(), nullable=True))
    op.add_column("prospects", sa.Column("score_detalhe", sa.JSON(), nullable=True))
    op.add_column("prospects", sa.Column("score_calculado_em", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_prospects_score", "prospects", ["score"])

    op.drop_constraint("ck_prospects_status_valido", "prospects", type_="check")
    op.create_check_constraint(
        "ck_prospects_status_valido",
        "prospects",
        "status IN (" + ", ".join(f"'{v}'" for v in STATUS_PROSPECT_NOVOS) + ")",
    )

    op.create_table(
        "politicas_prospeccao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("aprovacao_automatica_ativa", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("score_minimo_aprovacao", sa.Integer(), nullable=True),
        sa.Column("atualizado_por", sa.String(length=254), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", name="uq_politica_prospeccao_organizacao"),
        sa.CheckConstraint(
            "score_minimo_aprovacao IS NULL OR (score_minimo_aprovacao >= 0 AND score_minimo_aprovacao <= 100)",
            name="ck_politica_prospeccao_score_minimo",
        ),
    )
    op.create_index("ix_politicas_prospeccao_organizacao_id", "politicas_prospeccao", ["organizacao_id"])
    op.execute('ALTER TABLE "politicas_prospeccao" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "politicas_prospeccao" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "politicas_prospeccao" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "politicas_prospeccao" TO inpi_app')


def downgrade() -> None:
    op.drop_table("politicas_prospeccao")
    op.drop_constraint("ck_prospects_status_valido", "prospects", type_="check")
    op.create_check_constraint(
        "ck_prospects_status_valido",
        "prospects",
        "status IN (" + ", ".join(f"'{v}'" for v in STATUS_PROSPECT_ANTIGOS) + ")",
    )
    op.drop_index("ix_prospects_score", table_name="prospects")
    op.drop_column("prospects", "score_calculado_em")
    op.drop_column("prospects", "score_detalhe")
    op.drop_column("prospects", "score")

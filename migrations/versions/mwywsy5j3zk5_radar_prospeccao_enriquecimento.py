"""radar de prospeccao: presenca digital e historico de enriquecimento (Fase 3)

Revision ID: mwywsy5j3zk5
Revises: 98czgjqcsywi

Fase 3 do roadmap do Radar de Prospeccao (03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md). Fonte escolhida pelo
usuario: so verificar se o site do prospect responde (sem provedor pago) --
nao descobre nada que o prospect nao trouxe (sem redes sociais).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "mwywsy5j3zk5"
down_revision: str | None = "98czgjqcsywi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.add_column("prospects", sa.Column("presenca_digital", sa.JSON(), nullable=True))

    op.create_table(
        "prospect_enriquecimentos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("prospect_id", sa.BigInteger(), nullable=False),
        sa.Column("provedor", sa.String(length=30), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sucesso", sa.Boolean(), nullable=False),
        sa.Column("erro", sa.String(length=120), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prospect_id"], ["prospects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prospect_enriquecimentos_organizacao_id", "prospect_enriquecimentos", ["organizacao_id"])
    op.create_index("ix_prospect_enriquecimentos_prospect_id", "prospect_enriquecimentos", ["prospect_id"])
    op.create_index(
        "ix_prospect_enriquecimentos_prospect_provedor_criado",
        "prospect_enriquecimentos",
        ["prospect_id", "provedor", "criado_em"],
    )
    op.execute('ALTER TABLE "prospect_enriquecimentos" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "prospect_enriquecimentos" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "prospect_enriquecimentos" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "prospect_enriquecimentos" TO inpi_app')


def downgrade() -> None:
    op.drop_table("prospect_enriquecimentos")
    op.drop_column("prospects", "presenca_digital")

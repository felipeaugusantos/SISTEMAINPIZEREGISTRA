"""notas fiscais de servico eletronica -- NFS-e (FASE7-6)

Revision ID: zu97f5x9i408
Revises: zt86e4w8h397

Achado FASE7-6 da auditoria (04/09/2026): nao existia emissao de NFS-e --
so ReciboFinanceiro (recibo interno, sem valor fiscal). notas_fiscais_servico
registra cada tentativa/emissao real, atras da interface de adaptador em
app/nfse.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zu97f5x9i408"
down_revision: str | None = "zt86e4w8h397"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "notas_fiscais_servico",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lancamento_id", sa.BigInteger(), nullable=False),
        sa.Column("adaptador", sa.String(length=30), nullable=False),
        sa.Column("numero", sa.String(length=60), nullable=True),
        sa.Column("codigo_verificacao", sa.String(length=60), nullable=True),
        sa.Column("valor", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="emitida"),
        sa.Column("erro_detalhe", sa.Text(), nullable=True),
        sa.Column("emitida_por", sa.String(length=254), nullable=False),
        sa.Column("emitida_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cancelada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelada_por", sa.String(length=254), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lancamento_id"], ["lancamentos_financeiros.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notas_fiscais_servico_organizacao_id", "notas_fiscais_servico", ["organizacao_id"])
    op.create_index("ix_notas_fiscais_servico_lancamento_id", "notas_fiscais_servico", ["lancamento_id"])
    op.create_index("ix_notas_fiscais_servico_status", "notas_fiscais_servico", ["status"])
    op.create_index("ix_notas_fiscais_servico_emitida_em", "notas_fiscais_servico", ["emitida_em"])

    op.execute('ALTER TABLE "notas_fiscais_servico" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "notas_fiscais_servico" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "notas_fiscais_servico" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "notas_fiscais_servico" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("notas_fiscais_servico")

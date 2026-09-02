"""politica juridica: evidencia de conclusao e segunda pessoa em prazo critico

Revision ID: af46g7h8i074
Revises: ae35f6g7h963

Fase 2 da auditoria do modulo juridico (01/09/2026), achado 5.3: hoje a
mesma pessoa pode criar, confirmar e concluir um prazo sozinha, sem
exigencia de evidencia nem segunda conferencia para prazos criticos. Esta
migration so cria a configuracao (desligada por padrao); a validacao em si
esta em app/api/juridico.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "af46g7h8i074"
down_revision: str | None = "ae35f6g7h963"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "politicas_juridicas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("exigir_evidencia_conclusao", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("exigir_segunda_pessoa_critico", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("atualizado_por", sa.String(254), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", name="uq_politica_juridica_organizacao"),
    )
    op.create_index("ix_politicas_juridicas_organizacao_id", "politicas_juridicas", ["organizacao_id"])

    op.execute('ALTER TABLE "politicas_juridicas" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "politicas_juridicas" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "politicas_juridicas" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "politicas_juridicas" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("politicas_juridicas")

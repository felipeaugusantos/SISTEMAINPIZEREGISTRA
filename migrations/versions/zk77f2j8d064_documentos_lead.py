"""documentos (metadados) por lead — procuração, GRU, protocolo, oposição, certificado

Revision ID: zk77f2j8d064
Revises: zj66e1i7c953
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zk77f2j8d064"
down_revision: str | None = "zj66e1i7c953"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "documentos_lead",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("numero", sa.String(60), nullable=True),
        sa.Column("data", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pendente", nullable=False),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organizacao_id", "lead_id", "tipo", name="uq_documento_lead_tipo"
        ),
    )
    op.create_index("ix_documentos_lead_organizacao_id", "documentos_lead", ["organizacao_id"])
    op.create_index("ix_documentos_lead_lead_id", "documentos_lead", ["lead_id"])

    op.execute('ALTER TABLE "documentos_lead" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "documentos_lead" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "documentos_lead" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "documentos_lead" TO inpi_app')


def downgrade() -> None:
    op.drop_table("documentos_lead")

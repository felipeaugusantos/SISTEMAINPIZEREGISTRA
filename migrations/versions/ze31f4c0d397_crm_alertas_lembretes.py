"""crm alertas lembretes e documento do cliente

Revision ID: ze31f4c0d397
Revises: zd30e3b9c286
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ze31f4c0d397"
down_revision: str | None = "zd30e3b9c286"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.add_column("leads", sa.Column("documento", sa.String(30), nullable=True))
    op.create_index("ix_leads_documento", "leads", ["documento"])

    op.create_table(
        "lembretes_crm",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("responsavel_id", sa.BigInteger(), nullable=True),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("prioridade", sa.String(10), server_default="media", nullable=False),
        sa.Column("titulo", sa.String(180), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("lembrar_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default="pendente", nullable=False),
        sa.Column("criado_por_id", sa.BigInteger(), nullable=True),
        sa.Column("criado_por", sa.String(254), nullable=False),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluido_por", sa.String(254), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "tipo IN ('retorno','atualizar_cadastro','enviar_proposta','cobrar_documentos','acompanhar_processo','outro')",
            name="ck_lembretes_crm_tipo",
        ),
        sa.CheckConstraint(
            "prioridade IN ('baixa','media','alta')", name="ck_lembretes_crm_prioridade"
        ),
        sa.CheckConstraint(
            "status IN ('pendente','concluido','cancelado')", name="ck_lembretes_crm_status"
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["responsavel_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["criado_por_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in (
        "organizacao_id",
        "lead_id",
        "responsavel_id",
        "tipo",
        "prioridade",
        "lembrar_em",
        "status",
        "criado_em",
    ):
        op.create_index(f"ix_lembretes_crm_{coluna}", "lembretes_crm", [coluna])

    op.execute('ALTER TABLE "lembretes_crm" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "lembretes_crm" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "lembretes_crm" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "lembretes_crm" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("lembretes_crm")
    op.drop_index("ix_leads_documento", table_name="leads")
    op.drop_column("leads", "documento")

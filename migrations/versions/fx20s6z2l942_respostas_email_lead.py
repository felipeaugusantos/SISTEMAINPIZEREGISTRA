"""historico bidirecional de e-mail: guarda o conteudo da resposta do lead

Revision ID: fx20s6z2l942
Revises: ew19r5y1k831

Achado da auditoria completa do CRM (06/09/2026, item 21): o sistema ja
sabia QUE um lead respondeu (pausar_envios_pendentes_do_lead grava
respondido_em em envios_cadencia_email), mas nunca guardava O QUE ele
respondeu -- o corpo do e-mail era lido via IMAP so para extrair o
remetente (app/imap_polling.py) e descartado em seguida. Esta tabela
fecha essa lacuna, alimentada pelo mesmo job de polling ja existente
(nenhuma caixa de e-mail nova, nenhuma credencial nova).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fx20s6z2l942"
down_revision: str | None = "ew19r5y1k831"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "respostas_email_lead",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("remetente", sa.String(length=254), nullable=False),
        sa.Column("assunto", sa.String(length=255), nullable=True),
        sa.Column("corpo", sa.Text(), nullable=False),
        sa.Column("recebido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_respostas_email_lead_organizacao_id", "respostas_email_lead", ["organizacao_id"])
    op.create_index("ix_respostas_email_lead_lead_id", "respostas_email_lead", ["lead_id"])
    op.create_index("ix_respostas_email_lead_criado_em", "respostas_email_lead", ["criado_em"])
    op.execute('ALTER TABLE "respostas_email_lead" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "respostas_email_lead" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "respostas_email_lead" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "respostas_email_lead" TO inpi_app')


def downgrade() -> None:
    op.drop_table("respostas_email_lead")

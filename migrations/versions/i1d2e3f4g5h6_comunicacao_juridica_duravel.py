"""caixa de saida duravel da comunicacao juridica

Revision ID: i1d2e3f4g5h6
Revises: h0c1d2e3f4g5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i1d2e3f4g5h6"
down_revision: str | None = "h0c1d2e3f4g5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "organizacao_id = nullif(current_setting('app.organizacao_id', true), '')::bigint"
SUPERADMIN = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "saidas_email_juridico",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "notificacao_id",
            sa.BigInteger(),
            sa.ForeignKey("notificacoes_juridicas.id", ondelete="CASCADE"),
        ),
        sa.Column("chave", sa.String(180), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False, server_default="alerta_prazo"),
        sa.Column("destinatario", sa.String(254), nullable=False),
        sa.Column("assunto", sa.String(180), nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pendente"),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provedor", sa.String(40)),
        sa.Column("ultimo_erro", sa.String(120)),
        sa.Column("disponivel_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processando_em", sa.DateTime(timezone=True)),
        sa.Column("enviado_em", sa.DateTime(timezone=True)),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organizacao_id", "chave", name="uq_saida_email_juridico_org_chave"),
        sa.CheckConstraint(
            "status IN ('pendente','processando','enviado','falha')",
            name="ck_saida_email_juridico_status",
        ),
        sa.CheckConstraint("tentativas >= 0", name="ck_saida_email_juridico_tentativas"),
    )
    for coluna in (
        "organizacao_id",
        "notificacao_id",
        "tipo",
        "status",
        "disponivel_em",
        "enviado_em",
        "criado_em",
    ):
        op.create_index(f"ix_saidas_email_juridico_{coluna}", "saidas_email_juridico", [coluna])
    op.execute('ALTER TABLE "saidas_email_juridico" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "saidas_email_juridico" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "saidas_email_juridico" FOR ALL '
        f"USING ({TENANT} OR {SUPERADMIN}) WITH CHECK ({TENANT} OR {SUPERADMIN})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "saidas_email_juridico" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("saidas_email_juridico")

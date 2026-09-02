"""cadencias reais por e-mail: envio, rastreio de abertura e resposta

Revision ID: in24o6p7q873
Revises: hm13n5o6p762

Fase 9 do plano Leads/CRM (03/09/2026), achados L5/L6: aplicar_cadencia_lead
so pre-criava um LembreteCRM manual -- nenhum envio de fato acontecia sozinho.
Cria envios_cadencia_email para rastrear a execucao real do passo de canal
"email": agendamento, envio via SMTP, abertura (pixel) e resposta (se o
polling IMAP estiver configurado).

Usa o mesmo padrao de "leitura de bootstrap" ja usado em
ac13d4e5f741_habilita_rls_propostas_comerciais.py: o pixel de rastreio (GET
publico por token, sem tenant resolvido ainda) precisa de SELECT liberado
antes de aplicar_contexto_tenant; escrita sempre exige o tenant ja resolvido.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "in24o6p7q873"
down_revision: str | None = "hm13n5o6p762"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"
BOOTSTRAP = "NULLIF(current_setting('app.organizacao_id', true), '') IS NULL"


def upgrade() -> None:
    op.create_table(
        "envios_cadencia_email",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("cadencia_id", sa.BigInteger(), nullable=False),
        sa.Column("passo_id", sa.BigInteger(), nullable=False),
        sa.Column("agendado_para", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pendente", nullable=False),
        sa.Column("tentativas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rastreio_token_hash", sa.String(length=64), nullable=True),
        sa.Column("enviado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aberto_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("respondido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pausado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_erro", sa.String(length=300), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cadencia_id"], ["cadencias.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["passo_id"], ["cadencia_passos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organizacao_id", "lead_id", "cadencia_id", "passo_id", name="uq_envio_cadencia_passo"
        ),
    )
    for coluna in ("organizacao_id", "lead_id", "cadencia_id", "passo_id", "agendado_para", "status"):
        op.create_index(f"ix_envios_cadencia_email_{coluna}", "envios_cadencia_email", [coluna])
    op.create_index(
        "ix_envios_cadencia_email_rastreio_token_hash",
        "envios_cadencia_email",
        ["rastreio_token_hash"],
        unique=True,
    )
    op.execute('ALTER TABLE "envios_cadencia_email" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "envios_cadencia_email" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_bootstrap_read ON "envios_cadencia_email" FOR SELECT '
        f'USING ({BOOTSTRAP} OR {SUPER} OR organizacao_id = {TENANT})'
    )
    op.execute(
        'CREATE POLICY tenant_write ON "envios_cadencia_email" FOR ALL '
        f'USING ({SUPER} OR organizacao_id = {TENANT}) '
        f'WITH CHECK ({SUPER} OR organizacao_id = {TENANT})'
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "envios_cadencia_email" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS tenant_write ON "envios_cadencia_email"')
    op.execute('DROP POLICY IF EXISTS tenant_bootstrap_read ON "envios_cadencia_email"')
    op.drop_table("envios_cadencia_email")

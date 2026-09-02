"""consentimento estruturado e anonimizacao self-service (LGPD)

Revision ID: gl02m3n4o651
Revises: fk91l2m3n529

Fase 5 do plano Leads/CRM (03/09/2026), achados L13/L14: aceite_privacidade
era um bool unico (default True), sem distinguir consentimento realmente
capturado do titular (formulario publico) de consentimento presumido por um
atendente ao cadastrar um lead manualmente (origem=operador) -- ambos viravam
o mesmo True, sem data, versao do termo ou base legal registrada.

Adiciona os campos de consentimento estruturado em leads e uma tabela de
solicitacoes de anonimizacao (auto-atendimento do titular, confirmado por
token enviado por e-mail, no mesmo padrao de tokens_recuperacao_senha).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "gl02m3n4o651"
down_revision: str | None = "fk91l2m3n529"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.add_column("leads", sa.Column("consentimento_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("consentimento_versao_termo", sa.String(length=20), nullable=True))
    op.add_column("leads", sa.Column("consentimento_base_legal", sa.String(length=40), nullable=True))
    op.add_column("leads", sa.Column("consentimento_registrado_por", sa.BigInteger(), nullable=True))
    op.add_column("leads", sa.Column("anonimizado_em", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_leads_consentimento_base_legal", "leads", ["consentimento_base_legal"])
    op.create_index("ix_leads_consentimento_registrado_por", "leads", ["consentimento_registrado_por"])
    op.create_index("ix_leads_anonimizado_em", "leads", ["anonimizado_em"])
    op.create_foreign_key(
        "fk_leads_consentimento_registrado_por",
        "leads",
        "usuarios_operacoes",
        ["consentimento_registrado_por"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "solicitacoes_anonimizacao_lead",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leads_anonimizados", sa.Integer(), server_default="0", nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_solicitacao_anonimizacao_token"),
    )
    for coluna in ("organizacao_id", "email", "token_hash", "expira_em"):
        op.create_index(
            f"ix_solicitacoes_anonimizacao_lead_{coluna}",
            "solicitacoes_anonimizacao_lead",
            [coluna],
        )
    op.execute('ALTER TABLE "solicitacoes_anonimizacao_lead" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "solicitacoes_anonimizacao_lead" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "solicitacoes_anonimizacao_lead" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON "solicitacoes_anonimizacao_lead" TO inpi_app'
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "solicitacoes_anonimizacao_lead"')
    op.drop_table("solicitacoes_anonimizacao_lead")
    op.drop_constraint("fk_leads_consentimento_registrado_por", "leads", type_="foreignkey")
    op.drop_index("ix_leads_anonimizado_em", table_name="leads")
    op.drop_index("ix_leads_consentimento_registrado_por", table_name="leads")
    op.drop_index("ix_leads_consentimento_base_legal", table_name="leads")
    op.drop_column("leads", "anonimizado_em")
    op.drop_column("leads", "consentimento_registrado_por")
    op.drop_column("leads", "consentimento_base_legal")
    op.drop_column("leads", "consentimento_versao_termo")
    op.drop_column("leads", "consentimento_em")

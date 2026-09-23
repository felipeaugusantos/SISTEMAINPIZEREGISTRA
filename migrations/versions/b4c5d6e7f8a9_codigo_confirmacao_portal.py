"""código de confirmação por e-mail pra assinar no portal do cliente

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8

Achado médio da auditoria fina do Portal do Cliente (Fase 13.2,
23/09/2026): assinar_proposta_portal/assinar_documento_portal dependiam
só da sessão (12h) + CSRF -- sem reconfirmação no momento da assinatura,
diferente do fluxo público de aceite de proposta (código de 6 dígitos
por e-mail). Risco concreto: computador compartilhado com sessão do
portal ainda aberta permite qualquer pessoa presente assinar em nome do
cliente. Decisão do usuário: mesmo padrão de dupla validação por e-mail
do fluxo público, agora também no portal.

Tabela própria (não reaproveita os campos codigo_confirmacao_* de
propostas_comerciais, que são do fluxo público sem cliente_id) pra não
colidir se o mesmo cliente usar os dois fluxos quase ao mesmo tempo, e
pra também cobrir documentos_lead, que nunca teve esses campos.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "codigos_confirmacao_portal",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("cliente_id", sa.BigInteger(), nullable=False),
        sa.Column("recurso_tipo", sa.String(20), nullable=False),
        sa.Column("recurso_id", sa.BigInteger(), nullable=False),
        sa.Column("codigo_hash", sa.String(64), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enviado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cliente_id"], ["clientes_portal.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cliente_id", "recurso_tipo", "recurso_id", name="uq_codigo_confirmacao_portal_recurso"),
    )
    op.create_index(
        "ix_codigos_confirmacao_portal_organizacao_id", "codigos_confirmacao_portal", ["organizacao_id"]
    )
    op.create_index("ix_codigos_confirmacao_portal_cliente_id", "codigos_confirmacao_portal", ["cliente_id"])

    # Só acessada via ClientCsrfDep (tenant já resolvido, cliente autenticado) --
    # mesmo padrão "_direta" das demais tabelas do portal pós-login.
    op.execute('ALTER TABLE "codigos_confirmacao_portal" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "codigos_confirmacao_portal" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "codigos_confirmacao_portal" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "codigos_confirmacao_portal"')
    op.drop_index("ix_codigos_confirmacao_portal_cliente_id", table_name="codigos_confirmacao_portal")
    op.drop_index("ix_codigos_confirmacao_portal_organizacao_id", table_name="codigos_confirmacao_portal")
    op.drop_table("codigos_confirmacao_portal")

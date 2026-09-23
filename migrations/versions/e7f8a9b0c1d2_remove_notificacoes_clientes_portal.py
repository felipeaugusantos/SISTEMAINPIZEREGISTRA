"""remove notificacoes_clientes_portal (nunca consumida por nenhuma tela)

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1

Achado baixo da Fase 13.6 (auditoria fina do Portal do Cliente,
23/09/2026): a tabela, o modelo NotificacaoClientePortal e os endpoints
GET /v1/portal/notificacoes, PATCH /v1/portal/notificacoes/{id}/ler e
GET /v1/portal/eventos nunca foram ligados a nenhuma tela -- nada em
app.web jamais inseria uma linha aqui nem chamava esses endpoints.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("notificacoes_clientes_portal")


def downgrade() -> None:
    op.create_table(
        "notificacoes_clientes_portal",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("cliente_id", sa.BigInteger(), nullable=False),
        sa.Column("titulo", sa.String(180), nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column("lida_em", sa.DateTime(timezone=True)),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["cliente_id"], ["clientes_portal.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_notificacoes_clientes_portal_cliente_id", "notificacoes_clientes_portal", ["cliente_id"])
    op.create_index("ix_notificacoes_clientes_portal_criado_em", "notificacoes_clientes_portal", ["criado_em"])
    op.execute('ALTER TABLE "notificacoes_clientes_portal" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "notificacoes_clientes_portal" FORCE ROW LEVEL SECURITY')
    tenant = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
    super_ = "current_setting('app.superadmin', true) = 'true'"
    acesso = (
        "EXISTS (SELECT 1 FROM clientes_portal c WHERE c.id = cliente_id "
        f"AND ({super_} OR c.organizacao_id = {tenant}))"
    )
    op.execute(
        'CREATE POLICY tenant_isolation ON "notificacoes_clientes_portal" '
        f"USING ({super_} OR {acesso}) WITH CHECK ({super_} OR {acesso})"
    )

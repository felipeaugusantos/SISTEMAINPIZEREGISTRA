"""documentos entrega juridico: evidencia com hash de integridade

Revision ID: di79j0k1l307
Revises: ch68i9j0k296

Fase 7 da auditoria do modulo juridico (02/09/2026), achado 5.8:
registrar_entrega so aceitava protocolo/documento como texto livre, sem
anexo real nem hash -- qualquer texto passava como "evidencia", sem
verificacao alguma. Esta migration cria a tabela que guarda o anexo (quando
enviado) com hash SHA-256, mesmo padrao de documentos_ativos_pi/app.storage.
O anexo continua opcional: quem so registra o numero de protocolo em texto
continua funcionando como antes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "di79j0k1l307"
down_revision: str | None = "ch68i9j0k296"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "documentos_entrega_juridico",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("prazo_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("hash_documento", sa.String(64), nullable=False),
        sa.Column("caminho", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("criado_por", sa.String(254), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prazo_id"], ["prazos_juridicos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_documentos_entrega_juridico_organizacao_id", "documentos_entrega_juridico", ["organizacao_id"]
    )
    op.create_index("ix_documentos_entrega_juridico_prazo_id", "documentos_entrega_juridico", ["prazo_id"])
    op.create_index(
        "ix_documentos_entrega_juridico_hash_documento", "documentos_entrega_juridico", ["hash_documento"]
    )
    op.create_index(
        "ix_documentos_entrega_juridico_criado_em", "documentos_entrega_juridico", ["criado_em"]
    )

    op.execute('ALTER TABLE "documentos_entrega_juridico" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "documentos_entrega_juridico" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "documentos_entrega_juridico" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "documentos_entrega_juridico" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("documentos_entrega_juridico")

"""Mensagens de saída: horários de entrega e de leitura informados pelo gateway (recibos do WhatsApp).

Colunas anuláveis, sem valor padrão: linhas existentes seguem iguais.

Revision ID: zl85j1h8ie69
Revises: zk74i0g7hd58
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zl85j1h8ie69"
down_revision: str | None = "zk74i0g7hd58"
TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"

branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mensagens_saida", sa.Column("entregue_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mensagens_saida", sa.Column("lida_em", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "pausas_canal",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("sessao", sa.String(20), nullable=False),
        sa.Column("ate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("motivo", sa.String(120), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "sessao", name="uq_pausa_canal_sessao"),
    )
    op.create_index("ix_pausas_canal_organizacao_id", "pausas_canal", ["organizacao_id"])
    op.execute('ALTER TABLE "pausas_canal" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "pausas_canal" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "pausas_canal" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "pausas_canal" TO inpi_app')


def downgrade() -> None:
    op.drop_table("pausas_canal")
    op.drop_column("mensagens_saida", "lida_em")
    op.drop_column("mensagens_saida", "entregue_em")

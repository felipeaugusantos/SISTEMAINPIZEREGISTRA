"""Mensageria (WhatsApp): consentimento/supressão por telefone e fila de mensagens de saída.

Tabelas novas e isoladas por organização (RLS), sem efeito sobre dados existentes.

Revision ID: zk74i0g7hd58
Revises: zj63h9f6gc47
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zk74i0g7hd58"
down_revision: str | None = "zj63h9f6gc47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def _rls(tabela: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{tabela}" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{tabela}" TO inpi_app')


def upgrade() -> None:
    op.create_table(
        "consentimentos_canal",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("canal", sa.String(20), nullable=False),
        sa.Column("finalidade", sa.String(20), nullable=False),
        sa.Column("telefone", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("base_legal", sa.String(30), nullable=False),
        sa.Column("titular_tipo", sa.String(20), nullable=True),
        sa.Column("titular_id", sa.BigInteger(), nullable=True),
        sa.Column("origem", sa.String(60), nullable=True),
        sa.Column("evidencia", sa.JSON(), nullable=False),
        sa.Column("registrado_por", sa.String(254), nullable=True),
        sa.Column("concedido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revogado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "canal", "finalidade", "telefone", name="uq_consentimento_canal_telefone"),
    )
    for coluna in ("organizacao_id", "canal", "finalidade", "telefone", "status"):
        op.create_index(f"ix_consentimentos_canal_{coluna}", "consentimentos_canal", [coluna])
    _rls("consentimentos_canal")

    op.create_table(
        "mensagens_saida",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("canal", sa.String(20), nullable=False),
        sa.Column("finalidade", sa.String(20), nullable=False),
        sa.Column("telefone", sa.String(20), nullable=False),
        sa.Column("titular_tipo", sa.String(20), nullable=True),
        sa.Column("titular_id", sa.BigInteger(), nullable=True),
        sa.Column("corpo", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("motivo_bloqueio", sa.String(120), nullable=True),
        sa.Column("tentativas", sa.Integer(), nullable=False),
        sa.Column("agendada_para", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("enviada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_erro", sa.String(300), nullable=True),
        sa.Column("ultima_falha_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chave_idempotencia", sa.String(120), nullable=False),
        sa.Column("provedor_mensagem_id", sa.String(120), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "chave_idempotencia", name="uq_mensagem_saida_chave"),
    )
    for coluna in ("organizacao_id", "canal", "finalidade", "telefone", "status", "agendada_para", "enviada_em"):
        op.create_index(f"ix_mensagens_saida_{coluna}", "mensagens_saida", [coluna])
    _rls("mensagens_saida")


def downgrade() -> None:
    op.drop_table("mensagens_saida")
    op.drop_table("consentimentos_canal")

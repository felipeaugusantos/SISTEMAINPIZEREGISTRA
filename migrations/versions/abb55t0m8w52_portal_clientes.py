"""Add isolated client portal identities and content."""

import sqlalchemy as sa
from alembic import op

revision = "abb55t0m8w52"
down_revision = "aab44s9l7v41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clientes_portal",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(150), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("senha_hash", sa.Text(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("bloqueado_em", sa.DateTime(timezone=True)),
        sa.Column("bloqueado_motivo", sa.String(300)),
        sa.Column("ultimo_login_em", sa.DateTime(timezone=True)),
        sa.Column("criado_por", sa.BigInteger()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["criado_por"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("organizacao_id", "lead_id", name="uq_cliente_portal_lead"),
    )
    op.create_index("ix_clientes_portal_organizacao_id", "clientes_portal", ["organizacao_id"])
    op.create_index("ix_clientes_portal_lead_id", "clientes_portal", ["lead_id"])
    op.create_index("ix_clientes_portal_email", "clientes_portal", ["email"])
    op.create_index("ix_clientes_portal_ativo", "clientes_portal", ["ativo"])
    op.create_table(
        "sessoes_clientes_portal",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("cliente_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revogada_em", sa.DateTime(timezone=True)),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["cliente_id"], ["clientes_portal.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sessoes_clientes_portal_cliente_id", "sessoes_clientes_portal", ["cliente_id"])
    op.create_index("ix_sessoes_clientes_portal_token_hash", "sessoes_clientes_portal", ["token_hash"])
    op.create_index("ix_sessoes_clientes_portal_expira_em", "sessoes_clientes_portal", ["expira_em"])
    op.create_index("ix_sessoes_clientes_portal_revogada_em", "sessoes_clientes_portal", ["revogada_em"])
    for table in ("arquivos_clientes_portal", "mensagens_clientes_portal"):
        if table == "arquivos_clientes_portal":
            cols = [sa.Column("nome", sa.String(255), nullable=False), sa.Column("caminho", sa.Text(), nullable=False), sa.Column("content_type", sa.String(120)), sa.Column("tamanho", sa.BigInteger(), nullable=False, server_default="0")]
        else:
            cols = [sa.Column("autor_tipo", sa.String(20), nullable=False, server_default="cliente"), sa.Column("autor_id", sa.BigInteger()), sa.Column("mensagem", sa.Text(), nullable=False), sa.Column("lida_em", sa.DateTime(timezone=True))]
        op.create_table(table, sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("organizacao_id", sa.BigInteger(), nullable=False), sa.Column("lead_id", sa.BigInteger(), nullable=False), sa.Column("cliente_id", sa.BigInteger(), nullable=False), *cols, sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["cliente_id"], ["clientes_portal.id"], ondelete="CASCADE"))
        op.create_index(f"ix_{table}_cliente_id", table, ["cliente_id"])
        op.create_index(f"ix_{table}_lead_id", table, ["lead_id"])
    op.create_table("notificacoes_clientes_portal", sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("cliente_id", sa.BigInteger(), nullable=False), sa.Column("titulo", sa.String(180), nullable=False), sa.Column("mensagem", sa.Text(), nullable=False), sa.Column("lida_em", sa.DateTime(timezone=True)), sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.ForeignKeyConstraint(["cliente_id"], ["clientes_portal.id"], ondelete="CASCADE"))
    op.create_index("ix_notificacoes_clientes_portal_cliente_id", "notificacoes_clientes_portal", ["cliente_id"])


def downgrade() -> None:
    op.drop_table("notificacoes_clientes_portal")
    for table in ("mensagens_clientes_portal", "arquivos_clientes_portal"):
        op.drop_table(table)
    op.drop_table("sessoes_clientes_portal")
    op.drop_table("clientes_portal")

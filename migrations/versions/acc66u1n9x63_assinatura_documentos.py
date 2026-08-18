"""Add document versions, hashes and signatures."""

import sqlalchemy as sa
from alembic import op

revision = "acc66u1n9x63"
down_revision = "abb55t0m8w52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("versao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("hash_documento", sa.String(64)),
        sa.Column("validade_em", sa.Date()),
        sa.Column("obrigatorio", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("assinado_em", sa.DateTime(timezone=True)),
        sa.Column("assinado_ip_hash", sa.String(64)),
        sa.Column("assinado_por_cliente_id", sa.BigInteger()),
    ):
        op.add_column("documentos_lead", column)
    op.create_index("ix_documentos_lead_hash_documento", "documentos_lead", ["hash_documento"])
    op.create_index("ix_documentos_lead_validade_em", "documentos_lead", ["validade_em"])
    op.create_index("ix_documentos_lead_obrigatorio", "documentos_lead", ["obrigatorio"])
    op.create_table(
        "versoes_documentos_lead",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("documento_id", sa.BigInteger(), nullable=False), sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("hash_documento", sa.String(64), nullable=False), sa.Column("conteudo", sa.JSON(), nullable=False),
        sa.Column("criado_por_tipo", sa.String(20), nullable=False, server_default="operador"), sa.Column("criado_por_id", sa.BigInteger()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["documento_id"], ["documentos_lead.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_versoes_documentos_lead_documento_id", "versoes_documentos_lead", ["documento_id"])
    op.create_table(
        "assinaturas_documentos_lead",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("documento_id", sa.BigInteger(), nullable=False), sa.Column("cliente_id", sa.BigInteger()),
        sa.Column("versao", sa.Integer(), nullable=False), sa.Column("hash_documento", sa.String(64), nullable=False),
        sa.Column("ip_hash", sa.String(64)), sa.Column("assinado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("provedor", sa.String(30), nullable=False, server_default="interno"),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["documento_id"], ["documentos_lead.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_assinaturas_documentos_lead_documento_id", "assinaturas_documentos_lead", ["documento_id"])


def downgrade() -> None:
    op.drop_table("assinaturas_documentos_lead")
    op.drop_table("versoes_documentos_lead")
    for name in ("obrigatorio", "validade_em", "hash_documento", "versao", "assinado_em", "assinado_ip_hash", "assinado_por_cliente_id"):
        if name == "obrigatorio":
            op.drop_index("ix_documentos_lead_obrigatorio", table_name="documentos_lead")
        elif name == "validade_em":
            op.drop_index("ix_documentos_lead_validade_em", table_name="documentos_lead")
        elif name == "hash_documento":
            op.drop_index("ix_documentos_lead_hash_documento", table_name="documentos_lead")
        op.drop_column("documentos_lead", name)

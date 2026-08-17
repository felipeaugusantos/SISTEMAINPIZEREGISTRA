"""monitoramento preventivo, execucoes e historico de alertas"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision = "i96d1e2f3456"
down_revision: str | Sequence[str] | None = "h85c0d1e2f34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("colidencias_vigilancia", sa.Column("aprovado_por", sa.BigInteger(), nullable=True))
    op.add_column("colidencias_vigilancia", sa.Column("aprovado_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("colidencias_vigilancia", sa.Column("falso_positivo", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("colidencias_vigilancia", sa.Column("falso_positivo_motivo", sa.Text(), nullable=True))
    op.add_column("colidencias_vigilancia", sa.Column("comunicada_em", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_colidencias_vigilancia_falso_positivo", "colidencias_vigilancia", ["falso_positivo"])
    op.create_foreign_key("fk_colidencia_aprovado_por", "colidencias_vigilancia", "usuarios_operacoes", ["aprovado_por"], ["id"], ondelete="SET NULL")
    op.create_table(
        "vigilancia_execucoes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("chave", sa.String(120), nullable=False),
        sa.Column("frequencia", sa.String(20), nullable=False, server_default="semanal"),
        sa.Column("status", sa.String(20), nullable=False, server_default="executando"),
        sa.Column("encontrados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("criados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("falsos_positivos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resumo", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organizacao_id", "chave", name="uq_vigilancia_execucao_chave"),
    )
    op.create_index("ix_vigilancia_execucoes_organizacao_id", "vigilancia_execucoes", ["organizacao_id"])
    op.create_index("ix_vigilancia_execucoes_status", "vigilancia_execucoes", ["status"])
    op.create_table(
        "historico_alertas_vigilancia",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("colidencia_id", sa.BigInteger(), nullable=False),
        sa.Column("cliente_id", sa.BigInteger(), nullable=False),
        sa.Column("canal", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pendente"),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("justificativa", sa.Text(), nullable=False),
        sa.Column("detalhes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("enviado_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["colidencia_id"], ["colidencias_vigilancia.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cliente_id"], ["clientes_portal.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key", name="uq_historico_alerta_vigilancia_key"),
    )
    for col in ("organizacao_id", "colidencia_id", "cliente_id", "status", "criado_em"):
        op.create_index(f"ix_historico_alertas_vigilancia_{col}", "historico_alertas_vigilancia", [col])


def downgrade() -> None:
    for col in ("criado_em", "status", "cliente_id", "colidencia_id", "organizacao_id"):
        op.drop_index(f"ix_historico_alertas_vigilancia_{col}", table_name="historico_alertas_vigilancia")
    op.drop_table("historico_alertas_vigilancia")
    op.drop_index("ix_vigilancia_execucoes_status", table_name="vigilancia_execucoes")
    op.drop_index("ix_vigilancia_execucoes_organizacao_id", table_name="vigilancia_execucoes")
    op.drop_table("vigilancia_execucoes")
    op.drop_constraint("fk_colidencia_aprovado_por", "colidencias_vigilancia", type_="foreignkey")
    op.drop_index("ix_colidencias_vigilancia_falso_positivo", table_name="colidencias_vigilancia")
    for col in ("comunicada_em", "falso_positivo_motivo", "falso_positivo", "aprovado_em", "aprovado_por"):
        op.drop_column("colidencias_vigilancia", col)

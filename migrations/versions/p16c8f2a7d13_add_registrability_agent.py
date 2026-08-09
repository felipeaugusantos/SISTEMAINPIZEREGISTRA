"""add versioned registrability agent executions

Revision ID: p16c8f2a7d13
Revises: o15b7e1f6c02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p16c8f2a7d13"
down_revision: str | None = "o15b7e1f6c02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"
BOOTSTRAP = "NULLIF(current_setting('app.organizacao_id', true), '') IS NULL"


def upgrade() -> None:
    op.create_table(
        "execucoes_agente_registrabilidade",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("pesquisa_id", sa.String(length=36), nullable=False),
        sa.Column("versao_agente", sa.String(length=40), nullable=False),
        sa.Column("hash_entrada", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("decisao", sa.String(length=40), nullable=False),
        sa.Column("abstencao", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("cobertura", sa.Float(), server_default="0", nullable=False),
        sa.Column("confianca", sa.Float(), nullable=True),
        sa.Column("probabilidade_deferimento", sa.Float(), nullable=True),
        sa.Column("probabilidade_inferior", sa.Float(), nullable=True),
        sa.Column("probabilidade_superior", sa.Float(), nullable=True),
        sa.Column("nivel_risco", sa.String(length=20), nullable=True),
        sa.Column("pontuacao_risco", sa.Integer(), nullable=True),
        sa.Column("motivos", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column(
            "fatores_principais", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False
        ),
        sa.Column("entrada_estruturada", sa.JSON(), nullable=False),
        sa.Column("evidencias", sa.JSON(), nullable=False),
        sa.Column("regras", sa.JSON(), nullable=False),
        sa.Column("versoes_fontes", sa.JSON(), nullable=False),
        sa.Column("numero_pedido", sa.String(length=50), nullable=True),
        sa.Column("resultado_real", sa.String(length=30), nullable=True),
        sa.Column("resultado_fundamento", sa.String(length=50), nullable=True),
        sa.Column("resultado_data_referencia", sa.Date(), nullable=True),
        sa.Column("resultado_numero_rpi", sa.Integer(), nullable=True),
        sa.Column("resultado_sincronizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["organizacao_id"], ["organizacoes.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas_marca.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pesquisa_id", "hash_entrada", name="uq_agente_registrabilidade_pesquisa_hash"
        ),
    )
    for coluna in (
        "organizacao_id",
        "pesquisa_id",
        "versao_agente",
        "hash_entrada",
        "status",
        "decisao",
        "abstencao",
        "nivel_risco",
        "numero_pedido",
        "resultado_real",
        "criado_em",
    ):
        op.create_index(
            f"ix_execucoes_agente_registrabilidade_{coluna}",
            "execucoes_agente_registrabilidade",
            [coluna],
        )

    tabela = "execucoes_agente_registrabilidade"
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_bootstrap_read ON "{tabela}" FOR SELECT '
        f'USING ({BOOTSTRAP} OR {SUPER} OR organizacao_id = {TENANT})'
    )
    op.execute(
        f'CREATE POLICY tenant_write ON "{tabela}" FOR ALL '
        f'USING ({SUPER} OR organizacao_id = {TENANT}) '
        f'WITH CHECK ({SUPER} OR organizacao_id = {TENANT})'
    )


def downgrade() -> None:
    op.drop_table("execucoes_agente_registrabilidade")

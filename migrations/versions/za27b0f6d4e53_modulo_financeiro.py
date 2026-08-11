"""modulo financeiro operacional

Revision ID: za27b0f6d4e53
Revises: z26a9e5c3d42
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "za27b0f6d4e53"
down_revision: str | None = "z26a9e5c3d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def _tenant_table(nome: str) -> None:
    op.execute(f'ALTER TABLE "{nome}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{nome}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{nome}" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{nome}" TO inpi_app')


def upgrade() -> None:
    op.create_table(
        "categorias_financeiras",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("tipo", sa.String(12), server_default="ambos", nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", "nome", "tipo", name="uq_categoria_financeira_org"),
    )
    op.create_table(
        "lancamentos_financeiros",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("empresa_id", sa.BigInteger(), nullable=True),
        sa.Column("categoria_id", sa.BigInteger(), nullable=True),
        sa.Column("tipo", sa.String(12), nullable=False),
        sa.Column("descricao", sa.String(240), nullable=False),
        sa.Column("documento", sa.String(80), nullable=True),
        sa.Column("competencia", sa.Date(), nullable=False),
        sa.Column("valor_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(20), server_default="aberto", nullable=False),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("criado_por_id", sa.BigInteger(), nullable=True),
        sa.Column("criado_por", sa.String(254), nullable=False),
        sa.Column("cancelado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelado_por", sa.String(254), nullable=True),
        sa.Column("cancelamento_motivo", sa.Text(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas_crm.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["categoria_id"], ["categorias_financeiras.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "parcelas_financeiras",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("lancamento_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("vencimento", sa.Date(), nullable=False),
        sa.Column("valor", sa.Numeric(14, 2), nullable=False),
        sa.Column("valor_pago", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("status", sa.String(20), server_default="aberta", nullable=False),
        sa.Column("pago_em", sa.Date(), nullable=True),
        sa.Column("forma_pagamento", sa.String(50), nullable=True),
        sa.Column("observacoes_baixa", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["lancamento_id"], ["lancamentos_financeiros.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lancamento_id", "numero", name="uq_parcela_financeira_numero"),
    )
    for tabela, colunas in {
        "categorias_financeiras": ("organizacao_id", "nome", "tipo", "ativo"),
        "lancamentos_financeiros": (
            "organizacao_id",
            "empresa_id",
            "categoria_id",
            "tipo",
            "status",
            "competencia",
            "criado_em",
        ),
        "parcelas_financeiras": (
            "organizacao_id",
            "lancamento_id",
            "vencimento",
            "status",
            "pago_em",
        ),
    }.items():
        for coluna in colunas:
            op.create_index(f"ix_{tabela}_{coluna}", tabela, [coluna])
        _tenant_table(tabela)
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")
    op.execute("""
        INSERT INTO permissoes_operacoes (chave, modulo, nome, descricao, ordem) VALUES
        ('finance.view','Financeiro','Visualizar financeiro','Consultar contas e indicadores.',24),
        ('finance.manage','Financeiro','Gerenciar lançamentos',
         'Criar contas e registrar baixas.',25),
        ('finance.approve','Financeiro','Aprovar ajustes',
         'Cancelar lançamentos e estornar baixas com justificativa.',26),
        ('finance.export','Financeiro','Exportar financeiro','Exportar lançamentos em CSV.',27)
        ON CONFLICT (chave) DO NOTHING
    """)
    op.execute(
        "UPDATE planos_saas SET modulos = modulos::jsonb || '[\"financeiro\"]'::jsonb "
        "WHERE NOT modulos::jsonb ? 'financeiro'"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM usuario_permissoes WHERE permissao_id IN "
        "(SELECT id FROM permissoes_operacoes WHERE chave LIKE 'finance.%')"
    )
    op.execute("DELETE FROM permissoes_operacoes WHERE chave LIKE 'finance.%'")
    op.drop_table("parcelas_financeiras")
    op.drop_table("lancamentos_financeiros")
    op.drop_table("categorias_financeiras")

"""governanca e observabilidade da operacao juridica

Revision ID: h0c1d2e3f4g5
Revises: g9b0c1d2e3f4

Fases 1 a 4 da auditoria de Operacao Juridica (25/09/2026): catalogo
homologado de regras de prazo, excecoes oficiais de calendario, margem
operacional configuravel, revisao humana de historico, metadados de
documentos e ledger de execucoes do motor.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h0c1d2e3f4g5"
down_revision: str | None = "g9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "organizacao_id = nullif(current_setting('app.organizacao_id', true), '')::bigint"
SUPERADMIN = "current_setting('app.superadmin', true) = 'true'"


def _rls_tenant(tabela: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{tabela}" FOR ALL '
        f"USING ({TENANT} OR {SUPERADMIN}) WITH CHECK ({TENANT} OR {SUPERADMIN})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{tabela}" TO inpi_app')


def _rls_global(tabela: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY {tabela}_read ON "{tabela}" FOR SELECT USING (true)')
    op.execute(f'CREATE POLICY {tabela}_insert ON "{tabela}" FOR INSERT WITH CHECK ({SUPERADMIN})')
    op.execute(
        f'CREATE POLICY {tabela}_update ON "{tabela}" FOR UPDATE '
        f"USING ({SUPERADMIN}) WITH CHECK ({SUPERADMIN})"
    )
    op.execute(f'CREATE POLICY {tabela}_delete ON "{tabela}" FOR DELETE USING ({SUPERADMIN})')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{tabela}" TO inpi_app')


def upgrade() -> None:
    op.add_column("prazos_juridicos", sa.Column("vencimento_operacional_em", sa.DateTime(timezone=True)))
    op.add_column("prazos_juridicos", sa.Column("revisado_historico_em", sa.DateTime(timezone=True)))
    op.add_column("prazos_juridicos", sa.Column("revisado_historico_por", sa.String(254)))
    op.create_index(
        "ix_prazos_juridicos_vencimento_operacional_em",
        "prazos_juridicos",
        ["vencimento_operacional_em"],
    )

    op.add_column(
        "politicas_juridicas",
        sa.Column("exigir_responsavel_confirmacao", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "politicas_juridicas",
        sa.Column("exigir_checklist_conclusao", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "politicas_juridicas",
        sa.Column("margem_operacional_dias", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_politica_juridica_margem_operacional",
        "politicas_juridicas",
        "margem_operacional_dias BETWEEN 0 AND 30",
    )

    op.add_column("documentos_entrega_juridico", sa.Column("tamanho_bytes", sa.BigInteger()))

    op.create_table(
        "regras_prazo_juridico",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("codigo_despacho", sa.String(12), nullable=False),
        sa.Column("descricao_oficial", sa.String(300), nullable=False),
        sa.Column("tipo_prazo", sa.String(40), nullable=False),
        sa.Column("acao", sa.String(180), nullable=False),
        sa.Column("dias_prazo", sa.Integer(), nullable=False),
        sa.Column("contagem", sa.String(20), nullable=False, server_default="corridos"),
        sa.Column("vigencia_inicio", sa.Date(), nullable=False),
        sa.Column("vigencia_fim", sa.Date()),
        sa.Column("fonte_legal", sa.Text(), nullable=False),
        sa.Column("checklist", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidencias_exigidas", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confianca", sa.String(20), nullable=False, server_default="homologada"),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("aprovado_por", sa.String(254), nullable=False),
        sa.Column("aprovado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("codigo_despacho", "vigencia_inicio", name="uq_regra_prazo_codigo_vigencia"),
        sa.CheckConstraint("dias_prazo BETWEEN 0 AND 3650", name="ck_regra_prazo_dias"),
        sa.CheckConstraint("contagem IN ('corridos','uteis')", name="ck_regra_prazo_contagem"),
        sa.CheckConstraint("confianca IN ('rascunho','homologada')", name="ck_regra_prazo_confianca"),
        sa.CheckConstraint("vigencia_fim IS NULL OR vigencia_fim > vigencia_inicio", name="ck_regra_prazo_vigencia"),
    )
    for coluna in ("codigo_despacho", "tipo_prazo", "vigencia_inicio", "ativo"):
        op.create_index(f"ix_regras_prazo_juridico_{coluna}", "regras_prazo_juridico", [coluna])
    _rls_global("regras_prazo_juridico")

    op.create_table(
        "excecoes_calendario_juridico",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("data_inicio", sa.Date(), nullable=False),
        sa.Column("data_fim", sa.Date(), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("descricao", sa.String(300), nullable=False),
        sa.Column("fonte_oficial", sa.Text(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("aprovado_por", sa.String(254), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("data_inicio", "data_fim", "tipo", name="uq_excecao_calendario_periodo_tipo"),
        sa.CheckConstraint("data_fim >= data_inicio", name="ck_excecao_calendario_periodo"),
    )
    for coluna in ("data_inicio", "data_fim", "ativo"):
        op.create_index(f"ix_excecoes_calendario_juridico_{coluna}", "excecoes_calendario_juridico", [coluna])
    _rls_global("excecoes_calendario_juridico")

    op.create_table(
        "execucoes_motor_juridico",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("concluido_em", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="executando"),
        sa.Column("resultado", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("erro", sa.Text()),
        sa.CheckConstraint("status IN ('executando','sucesso','falha')", name="ck_execucao_motor_juridico_status"),
    )
    for coluna in ("organizacao_id", "iniciado_em", "status"):
        op.create_index(f"ix_execucoes_motor_juridico_{coluna}", "execucoes_motor_juridico", [coluna])
    _rls_tenant("execucoes_motor_juridico")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("execucoes_motor_juridico")
    op.drop_table("excecoes_calendario_juridico")
    op.drop_table("regras_prazo_juridico")
    op.drop_column("documentos_entrega_juridico", "tamanho_bytes")
    op.drop_constraint("ck_politica_juridica_margem_operacional", "politicas_juridicas", type_="check")
    op.drop_column("politicas_juridicas", "margem_operacional_dias")
    op.drop_column("politicas_juridicas", "exigir_checklist_conclusao")
    op.drop_column("politicas_juridicas", "exigir_responsavel_confirmacao")
    op.drop_index("ix_prazos_juridicos_vencimento_operacional_em", table_name="prazos_juridicos")
    op.drop_column("prazos_juridicos", "revisado_historico_por")
    op.drop_column("prazos_juridicos", "revisado_historico_em")
    op.drop_column("prazos_juridicos", "vencimento_operacional_em")

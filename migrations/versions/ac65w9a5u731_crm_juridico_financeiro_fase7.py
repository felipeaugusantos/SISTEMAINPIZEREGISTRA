"""integridade operacional de CRM, jurídico e financeiro

Revision ID: ac65w9a5u731
Revises: ab54v8z4t620
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ac65w9a5u731"
down_revision: str | None = "ab54v8z4t620"
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


def _unique(tabela: str, nome: str, colunas: list[str]) -> None:
    op.create_unique_constraint(nome, tabela, colunas)


def _fk(tabela: str, nome: str, locais: list[str], remota: str, remotas: list[str]) -> None:
    op.create_foreign_key(nome, tabela, remota, locais, remotas)


def upgrade() -> None:
    op.create_table(
        "politicas_crm",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("exigir_responsavel", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("atribuir_ao_operador", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("exigir_proxima_acao", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("dias_proxima_acao_padrao", sa.Integer(), nullable=True),
        sa.Column("atualizado_por", sa.String(254), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dias_proxima_acao_padrao IS NULL OR "
            "(dias_proxima_acao_padrao >= 0 AND dias_proxima_acao_padrao <= 365)",
            name="ck_politica_crm_dias_proxima_acao",
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organizacao_id", name="uq_politica_crm_organizacao"),
    )
    op.create_index("ix_politicas_crm_organizacao_id", "politicas_crm", ["organizacao_id"])

    op.create_table(
        "eventos_dominio",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("dominio", sa.String(20), nullable=False),
        sa.Column("tipo", sa.String(60), nullable=False),
        sa.Column("versao", sa.Integer(), server_default="1", nullable=False),
        sa.Column("entidade_tipo", sa.String(40), nullable=False),
        sa.Column("entidade_id", sa.String(64), nullable=False),
        sa.Column("ator_id", sa.BigInteger(), nullable=True),
        sa.Column("ator", sa.String(254), nullable=False),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=True),
        sa.Column(
            "ocorrido_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "dominio IN ('crm','juridico','financeiro')", name="ck_evento_operacional_dominio"
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ator_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organizacao_id",
            "dominio",
            "idempotency_key",
            name="uq_evento_operacional_idempotencia",
        ),
    )
    for coluna in (
        "organizacao_id",
        "dominio",
        "tipo",
        "entidade_tipo",
        "entidade_id",
        "ocorrido_em",
    ):
        op.create_index(f"ix_eventos_dominio_{coluna}", "eventos_dominio", [coluna])

    op.add_column("lembretes_crm", sa.Column("idempotency_key", sa.String(180), nullable=True))
    op.create_index("ix_lembretes_crm_idempotency_key", "lembretes_crm", ["idempotency_key"])
    op.create_unique_constraint(
        "uq_lembrete_crm_idempotencia", "lembretes_crm", ["organizacao_id", "idempotency_key"]
    )
    for coluna, tipo in (
        ("confirmado_por_id", sa.BigInteger()),
        ("confirmado_por", sa.String(254)),
        ("confirmado_em", sa.DateTime(timezone=True)),
        ("confirmacao_origem", sa.String(30)),
        ("confirmacao_observacoes", sa.Text()),
    ):
        op.add_column("prazos_juridicos", sa.Column(coluna, tipo, nullable=True))
    op.create_foreign_key(
        "fk_prazo_confirmado_por",
        "prazos_juridicos",
        "usuarios_operacoes",
        ["confirmado_por_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_prazos_juridicos_confirmado_em", "prazos_juridicos", ["confirmado_em"])
    op.execute(
        "UPDATE prazos_juridicos SET confirmado_por = "
        "COALESCE(concluido_por, criado_por, 'sistema'), "
        "confirmado_em = COALESCE(concluido_em, criado_em), "
        "confirmacao_origem = CASE WHEN origem = 'manual' "
        "THEN 'legado_manual' ELSE 'motor_legado' END, "
        "confirmacao_observacoes = 'Confirmação migrada do histórico anterior à Fase 7.' "
        "WHERE confirmado"
    )

    # Chaves candidatas compostas permitem que o banco valide o tenant do filho.
    for tabela, nome, colunas in (
        ("empresas_crm", "uq_empresa_crm_org_id", ["organizacao_id", "id"]),
        ("contatos", "uq_contato_org_empresa_id", ["organizacao_id", "empresa_id", "id"]),
        ("leads", "uq_lead_org_id", ["organizacao_id", "id"]),
        ("pesquisas_marca", "uq_pesquisa_org_id", ["organizacao_id", "id"]),
        ("usuarios_operacoes", "uq_usuario_org_id", ["organizacao_id", "id"]),
        ("processos_monitorados", "uq_monitorado_org_id", ["organizacao_id", "id"]),
        ("prazos_juridicos", "uq_prazo_org_id", ["organizacao_id", "id"]),
        ("categorias_financeiras", "uq_categoria_fin_org_id", ["organizacao_id", "id"]),
        ("formas_pagamento_financeiras", "uq_forma_fin_org_id", ["organizacao_id", "id"]),
        ("lancamentos_financeiros", "uq_lancamento_fin_org_id", ["organizacao_id", "id"]),
        ("parcelas_financeiras", "uq_parcela_fin_org_id", ["organizacao_id", "id"]),
    ):
        _unique(tabela, nome, colunas)

    op.create_check_constraint(
        "ck_lead_contato_exige_empresa", "leads", "contato_id IS NULL OR empresa_id IS NOT NULL"
    )
    for tabela, nome, locais, remota, remotas in (
        (
            "contatos",
            "fk_contato_empresa_tenant",
            ["organizacao_id", "empresa_id"],
            "empresas_crm",
            ["organizacao_id", "id"],
        ),
        (
            "leads",
            "fk_lead_empresa_tenant",
            ["organizacao_id", "empresa_id"],
            "empresas_crm",
            ["organizacao_id", "id"],
        ),
        (
            "leads",
            "fk_lead_contato_empresa_tenant",
            ["organizacao_id", "empresa_id", "contato_id"],
            "contatos",
            ["organizacao_id", "empresa_id", "id"],
        ),
        (
            "pesquisas_marca",
            "fk_pesquisa_lead_tenant",
            ["organizacao_id", "lead_id"],
            "leads",
            ["organizacao_id", "id"],
        ),
        (
            "pesquisas_marca",
            "fk_pesquisa_empresa_tenant",
            ["organizacao_id", "empresa_id"],
            "empresas_crm",
            ["organizacao_id", "id"],
        ),
        (
            "contatos_lead",
            "fk_interacao_lead_tenant",
            ["organizacao_id", "lead_id"],
            "leads",
            ["organizacao_id", "id"],
        ),
        (
            "contatos_lead",
            "fk_interacao_empresa_tenant",
            ["organizacao_id", "empresa_id"],
            "empresas_crm",
            ["organizacao_id", "id"],
        ),
        (
            "lembretes_crm",
            "fk_lembrete_lead_tenant",
            ["organizacao_id", "lead_id"],
            "leads",
            ["organizacao_id", "id"],
        ),
        (
            "processos_monitorados",
            "fk_monitorado_empresa_tenant",
            ["organizacao_id", "empresa_id"],
            "empresas_crm",
            ["organizacao_id", "id"],
        ),
        (
            "prazos_juridicos",
            "fk_prazo_monitorado_tenant",
            ["organizacao_id", "processo_monitorado_id"],
            "processos_monitorados",
            ["organizacao_id", "id"],
        ),
        (
            "itens_checklist_prazo",
            "fk_checklist_prazo_tenant",
            ["organizacao_id", "prazo_id"],
            "prazos_juridicos",
            ["organizacao_id", "id"],
        ),
        (
            "notificacoes_juridicas",
            "fk_notificacao_prazo_tenant",
            ["organizacao_id", "prazo_id"],
            "prazos_juridicos",
            ["organizacao_id", "id"],
        ),
        (
            "eventos_juridicos",
            "fk_evento_juridico_monitorado_tenant",
            ["organizacao_id", "processo_monitorado_id"],
            "processos_monitorados",
            ["organizacao_id", "id"],
        ),
        (
            "eventos_juridicos",
            "fk_evento_juridico_prazo_tenant",
            ["organizacao_id", "prazo_id"],
            "prazos_juridicos",
            ["organizacao_id", "id"],
        ),
        (
            "lancamentos_financeiros",
            "fk_lancamento_empresa_tenant",
            ["organizacao_id", "empresa_id"],
            "empresas_crm",
            ["organizacao_id", "id"],
        ),
        (
            "lancamentos_financeiros",
            "fk_lancamento_categoria_tenant",
            ["organizacao_id", "categoria_id"],
            "categorias_financeiras",
            ["organizacao_id", "id"],
        ),
        (
            "lancamentos_financeiros",
            "fk_lancamento_forma_tenant",
            ["organizacao_id", "forma_pagamento_id"],
            "formas_pagamento_financeiras",
            ["organizacao_id", "id"],
        ),
        (
            "parcelas_financeiras",
            "fk_parcela_lancamento_tenant",
            ["organizacao_id", "lancamento_id"],
            "lancamentos_financeiros",
            ["organizacao_id", "id"],
        ),
        (
            "parcelas_financeiras",
            "fk_parcela_forma_tenant",
            ["organizacao_id", "forma_pagamento_id"],
            "formas_pagamento_financeiras",
            ["organizacao_id", "id"],
        ),
        (
            "historicos_financeiros",
            "fk_historico_lancamento_tenant",
            ["organizacao_id", "lancamento_id"],
            "lancamentos_financeiros",
            ["organizacao_id", "id"],
        ),
        (
            "historicos_financeiros",
            "fk_historico_parcela_tenant",
            ["organizacao_id", "parcela_id"],
            "parcelas_financeiras",
            ["organizacao_id", "id"],
        ),
    ):
        _fk(tabela, nome, locais, remota, remotas)

    for tabela in ("politicas_crm", "eventos_dominio"):
        _tenant_table(tabela)
    op.execute("GRANT USAGE, SELECT ON SEQUENCE politicas_crm_id_seq TO inpi_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE eventos_dominio_id_seq TO inpi_app")


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade da Fase 7 exige janela de manutenção e revisão dos vínculos operacionais"
    )

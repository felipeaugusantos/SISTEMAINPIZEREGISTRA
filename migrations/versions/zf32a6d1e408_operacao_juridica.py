"""operacao juridica, prazos e notificacoes

Revision ID: zf32a6d1e408
Revises: ze31f4c0d397
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zf32a6d1e408"
down_revision: str | None = "ze31f4c0d397"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def _rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{table}" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO inpi_app')


def upgrade() -> None:
    op.create_table(
        "prazos_juridicos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("processo_monitorado_id", sa.BigInteger(), nullable=False),
        sa.Column("movimentacao_origem_id", sa.BigInteger(), nullable=True),
        sa.Column("responsavel_id", sa.BigInteger(), nullable=True),
        sa.Column("escalonar_para_id", sa.BigInteger(), nullable=True),
        sa.Column("titulo", sa.String(180), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("tipo", sa.String(40), server_default="manifestacao", nullable=False),
        sa.Column("origem", sa.String(20), server_default="manual", nullable=False),
        sa.Column("contagem", sa.String(20), server_default="corridos", nullable=False),
        sa.Column("data_base", sa.Date(), nullable=False),
        sa.Column("dias_prazo", sa.Integer(), nullable=False),
        sa.Column("vencimento_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), server_default="pendente", nullable=False),
        sa.Column("prioridade", sa.String(10), server_default="media", nullable=False),
        sa.Column("confirmado", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("antecedencia_dias", sa.Integer(), server_default="7", nullable=False),
        sa.Column("escalonar_dias_antes", sa.Integer(), server_default="2", nullable=False),
        sa.Column("escalonado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluido_por", sa.String(254), nullable=True),
        sa.Column("criado_por", sa.String(254), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("contagem IN ('corridos','uteis')", name="ck_prazos_juridicos_contagem"),
        sa.CheckConstraint("origem IN ('manual','motor_rpi')", name="ck_prazos_juridicos_origem"),
        sa.CheckConstraint(
            "prioridade IN ('baixa','media','alta','critica')",
            name="ck_prazos_juridicos_prioridade",
        ),
        sa.CheckConstraint(
            "status IN ('aguardando_confirmacao','pendente','em_andamento',"
            "'concluido','cancelado')",
            name="ck_prazos_juridicos_status",
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["processo_monitorado_id"], ["processos_monitorados.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["movimentacao_origem_id"], ["movimentacoes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["responsavel_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["escalonar_para_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organizacao_id",
            "processo_monitorado_id",
            "movimentacao_origem_id",
            name="uq_prazo_juridico_movimentacao",
        ),
    )
    for column in (
        "organizacao_id",
        "processo_monitorado_id",
        "movimentacao_origem_id",
        "responsavel_id",
        "escalonar_para_id",
        "tipo",
        "origem",
        "data_base",
        "vencimento_em",
        "status",
        "prioridade",
        "confirmado",
        "criado_em",
    ):
        op.create_index(f"ix_prazos_juridicos_{column}", "prazos_juridicos", [column])

    op.create_table(
        "notificacoes_juridicas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("prazo_id", sa.BigInteger(), nullable=False),
        sa.Column("destinatario_id", sa.BigInteger(), nullable=True),
        sa.Column("chave", sa.String(120), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("titulo", sa.String(180), nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), server_default="nova", nullable=False),
        sa.Column("lida_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lida_por", sa.String(254), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('nova','lida','arquivada')", name="ck_notificacoes_juridicas_status"
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prazo_id"], ["prazos_juridicos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["destinatario_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "organizacao_id",
        "prazo_id",
        "destinatario_id",
        "chave",
        "tipo",
        "status",
        "criado_em",
    ):
        op.create_index(
            f"ix_notificacoes_juridicas_{column}",
            "notificacoes_juridicas",
            [column],
            unique=column == "chave",
        )

    op.create_table(
        "eventos_juridicos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("prazo_id", sa.BigInteger(), nullable=True),
        sa.Column("processo_monitorado_id", sa.BigInteger(), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("ator", sa.String(254), nullable=False),
        sa.Column("descricao", sa.String(500), nullable=False),
        sa.Column("detalhes", sa.JSON(), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prazo_id"], ["prazos_juridicos.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["processo_monitorado_id"], ["processos_monitorados.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "organizacao_id",
        "prazo_id",
        "processo_monitorado_id",
        "tipo",
        "ator",
        "criado_em",
    ):
        op.create_index(f"ix_eventos_juridicos_{column}", "eventos_juridicos", [column])

    for table in ("prazos_juridicos", "notificacoes_juridicas", "eventos_juridicos"):
        _rls(table)
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("eventos_juridicos")
    op.drop_table("notificacoes_juridicas")
    op.drop_table("prazos_juridicos")

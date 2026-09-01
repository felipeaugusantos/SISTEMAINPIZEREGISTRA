"""pre-cadastro de processo ainda nao publicado na RPI

Revision ID: ab12c3d4e630
Revises: zz32u7y3s519
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ab12c3d4e630"
down_revision: str | None = "ac74e9f0b125"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "pre_cadastros_processo",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.String(50), nullable=False),
        sa.Column("numero_normalizado", sa.String(50), nullable=False),
        sa.Column("titular", sa.String(300), nullable=False),
        sa.Column("empresa_id", sa.BigInteger(), nullable=True),
        sa.Column("responsavel_id", sa.BigInteger(), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="aguardando", nullable=False),
        sa.Column("titular_divergente", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("criado_por", sa.String(254), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("vinculado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processo_monitorado_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas_crm.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["responsavel_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["processo_monitorado_id"], ["processos_monitorados.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('aguardando','vinculado','cancelado')",
            name="ck_pre_cadastro_processo_status",
        ),
    )
    for coluna in ("organizacao_id", "numero_normalizado", "empresa_id", "responsavel_id", "status"):
        op.create_index(f"ix_pre_cadastros_processo_{coluna}", "pre_cadastros_processo", [coluna])
    # Impede duplicar o mesmo numero pendente para a mesma organizacao (nao ha
    # restricao para numeros ja vinculados ou cancelados, para permitir reenvio).
    op.create_index(
        "uq_pre_cadastro_processo_pendente",
        "pre_cadastros_processo",
        ["organizacao_id", "numero_normalizado"],
        unique=True,
        postgresql_where=sa.text("status = 'aguardando'"),
    )

    op.execute('ALTER TABLE "pre_cadastros_processo" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "pre_cadastros_processo" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "pre_cadastros_processo" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "pre_cadastros_processo" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("pre_cadastros_processo")

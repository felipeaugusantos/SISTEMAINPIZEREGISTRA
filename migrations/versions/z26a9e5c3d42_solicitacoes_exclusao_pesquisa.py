"""solicitacoes auditaveis de exclusao de pesquisa

Revision ID: z26a9e5c3d42
Revises: y25f8d4b2c31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "z26a9e5c3d42"
down_revision: str | None = "y25f8d4b2c31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "solicitacoes_exclusao_pesquisa",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("pesquisa_id", sa.String(length=36), nullable=True),
        sa.Column("pesquisa_referencia", sa.String(length=36), nullable=False),
        sa.Column("marca", sa.String(length=200), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=True),
        sa.Column("solicitado_por_id", sa.BigInteger(), nullable=True),
        sa.Column("solicitado_por", sa.String(length=254), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pendente", nullable=False),
        sa.Column("decidido_por_id", sa.BigInteger(), nullable=True),
        sa.Column("decidido_por", sa.String(length=254), nullable=True),
        sa.Column("decisao_observacao", sa.Text(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas_marca.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["solicitado_por_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["decidido_por_id"], ["usuarios_operacoes.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in (
        "organizacao_id",
        "pesquisa_id",
        "pesquisa_referencia",
        "marca",
        "lead_id",
        "solicitado_por_id",
        "status",
        "criado_em",
    ):
        op.create_index(
            f"ix_solicitacoes_exclusao_pesquisa_{coluna}",
            "solicitacoes_exclusao_pesquisa",
            [coluna],
        )
    op.execute(
        "CREATE UNIQUE INDEX uq_exclusao_pesquisa_pendente "
        "ON solicitacoes_exclusao_pesquisa (organizacao_id, pesquisa_id) "
        "WHERE status = 'pendente' AND pesquisa_id IS NOT NULL"
    )
    op.execute('ALTER TABLE "solicitacoes_exclusao_pesquisa" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "solicitacoes_exclusao_pesquisa" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "solicitacoes_exclusao_pesquisa" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON "solicitacoes_exclusao_pesquisa" TO inpi_app'
    )
    op.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app')


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "solicitacoes_exclusao_pesquisa"')
    op.drop_table("solicitacoes_exclusao_pesquisa")

"""radar de prospeccao: lista de supressao (opt-out) LGPD

Revision ID: hk08e5rgstck
Revises: wp43q1s5d064

Achado FASE5-5 da auditoria (04/09/2026): nao existia nenhum mecanismo de
opt-out para dados de prospeccao (so Lead tinha, via
app/api/privacidade.py). Quem entra na lista nunca mais vira Prospect
nessa organizacao (checado em app/api/prospeccao.py::_esta_suprimido,
chamado pelo unico ponto de criacao de Prospect).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "hk08e5rgstck"
down_revision: str | None = "wp43q1s5d064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "supressoes_prospeccao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("cnpj", sa.String(length=18), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("motivo", sa.String(length=200), nullable=True),
        sa.Column("criado_por", sa.String(length=150), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("cnpj IS NOT NULL OR email IS NOT NULL", name="ck_supressao_prospeccao_identificador"),
    )
    op.create_index("ix_supressoes_prospeccao_organizacao_id", "supressoes_prospeccao", ["organizacao_id"])
    op.create_index("ix_supressoes_prospeccao_cnpj", "supressoes_prospeccao", ["cnpj"])
    op.create_index("ix_supressoes_prospeccao_email", "supressoes_prospeccao", ["email"])
    op.execute('ALTER TABLE "supressoes_prospeccao" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "supressoes_prospeccao" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "supressoes_prospeccao" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "supressoes_prospeccao" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("supressoes_prospeccao")

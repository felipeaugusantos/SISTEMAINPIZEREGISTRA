"""liga processo monitorado ao lead comercial de origem

Revision ID: ad24e5f6g852
Revises: ac13d4e5f741

Achado da auditoria do CRM: Lead.processo_numero era so texto solto, sem
integridade referencial com processos_monitorados -- a ligacao comercial ->
juridico dependia de o operador ter digitado o numero certo. Este campo
(nullable, preenchido automaticamente quando o numero bate e sempre editavel
manualmente) fecha essa lacuna sem tornar nada obrigatorio.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ad24e5f6g852"
down_revision: str | None = "ac13d4e5f741"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processos_monitorados",
        sa.Column("lead_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_processo_monitorado_lead",
        "processos_monitorados",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_processos_monitorados_lead_id", "processos_monitorados", ["lead_id"])


def downgrade() -> None:
    op.drop_index("ix_processos_monitorados_lead_id", table_name="processos_monitorados")
    op.drop_constraint("fk_processo_monitorado_lead", "processos_monitorados", type_="foreignkey")
    op.drop_column("processos_monitorados", "lead_id")

"""resultado e motivo de perda no lead

Revision ID: zt66o1s7m953
Revises: zs55n0r6l842
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zt66o1s7m953"
down_revision: str | None = "zs55n0r6l842"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("resultado", sa.String(12), nullable=True))
    op.add_column("leads", sa.Column("motivo_perda", sa.String(20), nullable=True))
    op.add_column("leads", sa.Column("motivo_perda_detalhe", sa.Text(), nullable=True))
    op.create_index("ix_leads_resultado", "leads", ["resultado"])
    op.create_index("ix_leads_motivo_perda", "leads", ["motivo_perda"])
    # Backfill: leads já convertidos/descartados recebem o desfecho coerente.
    op.execute("UPDATE leads SET resultado='ganho' WHERE status='convertido'")
    op.execute("UPDATE leads SET resultado='perdido' WHERE status='descartado'")


def downgrade() -> None:
    op.drop_index("ix_leads_motivo_perda", table_name="leads")
    op.drop_index("ix_leads_resultado", table_name="leads")
    op.drop_column("leads", "motivo_perda_detalhe")
    op.drop_column("leads", "motivo_perda")
    op.drop_column("leads", "resultado")

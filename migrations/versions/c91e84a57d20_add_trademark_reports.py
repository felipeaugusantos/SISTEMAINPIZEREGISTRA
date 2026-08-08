"""add trademark reports

Revision ID: c91e84a57d20
Revises: a7d13b2f9c44
Create Date: 2026-07-16 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c91e84a57d20"
down_revision: str | None = "a7d13b2f9c44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("empresa", sa.String(length=200)))
    op.add_column(
        "leads",
        sa.Column(
            "aceite_marketing",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_index("ix_leads_empresa", "leads", ["empresa"])
    op.create_table(
        "pesquisas_marca",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("marca", sa.String(length=200), nullable=False),
        sa.Column("tipo_pesquisa", sa.String(length=20), nullable=False),
        sa.Column("classe_nice", sa.String(length=2)),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pesquisas_marca_lead_id", "pesquisas_marca", ["lead_id"])
    op.create_index("ix_pesquisas_marca_marca", "pesquisas_marca", ["marca"])
    op.create_index(
        "ix_pesquisas_marca_tipo_pesquisa",
        "pesquisas_marca",
        ["tipo_pesquisa"],
    )
    op.create_index("ix_pesquisas_marca_classe_nice", "pesquisas_marca", ["classe_nice"])


def downgrade() -> None:
    op.drop_table("pesquisas_marca")
    op.drop_index("ix_leads_empresa", table_name="leads")
    op.drop_column("leads", "aceite_marketing")
    op.drop_column("leads", "empresa")

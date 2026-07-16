"""create leads

Revision ID: e31a865cb120
Revises: d9a4f21b730e
Create Date: 2026-07-16 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e31a865cb120"
down_revision: str | None = "d9a4f21b730e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("nome", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("telefone", sa.String(length=30), nullable=False),
        sa.Column("marca", sa.Text(), nullable=False),
        sa.Column("tipo_interesse", sa.String(length=10), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "novo",
                "em_contato",
                "convertido",
                "descartado",
                name="status_lead",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("aceite_privacidade", sa.Boolean(), nullable=False),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "tipo_interesse IS NULL OR tipo_interesse IN ('marca', 'patente')",
            name="ck_leads_tipo_interesse",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_leads_email"), "leads", ["email"])
    op.create_index(op.f("ix_leads_marca"), "leads", ["marca"])
    op.create_index(op.f("ix_leads_nome"), "leads", ["nome"])
    op.create_index(op.f("ix_leads_status"), "leads", ["status"])
    op.create_index(op.f("ix_leads_telefone"), "leads", ["telefone"])


def downgrade() -> None:
    op.drop_table("leads")

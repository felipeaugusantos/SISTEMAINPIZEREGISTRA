"""Add Stripe subscription checkout support for SaaS.

Revision ID: zc70f9a2b184
Revises: zb51f6a9c204
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zc70f9a2b184"
down_revision: str | None = "zb51f6a9c204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("planos_saas", sa.Column("stripe_price_mensal_id", sa.String(150), nullable=True))
    op.add_column("planos_saas", sa.Column("stripe_price_anual_id", sa.String(150), nullable=True))
    op.add_column("organizacoes", sa.Column("billing_subscription_id", sa.String(150), nullable=True))
    op.add_column("organizacoes", sa.Column("billing_intervalo", sa.String(10), nullable=True))
    op.create_index("ix_organizacoes_billing_subscription_id", "organizacoes", ["billing_subscription_id"])
    op.create_table(
        "eventos_assinatura_stripe",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("tipo", sa.String(100), nullable=False),
        sa.Column("processado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_eventos_assinatura_stripe_tipo", "eventos_assinatura_stripe", ["tipo"])
    op.execute("ALTER TABLE eventos_assinatura_stripe ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eventos_assinatura_stripe FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY rls_eventos_assinatura_stripe ON eventos_assinatura_stripe
        USING (current_setting('app.superadmin', true) = 'true')
        WITH CHECK (current_setting('app.superadmin', true) = 'true')"""
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS rls_eventos_assinatura_stripe ON eventos_assinatura_stripe")
    op.drop_index("ix_eventos_assinatura_stripe_tipo", table_name="eventos_assinatura_stripe")
    op.drop_table("eventos_assinatura_stripe")
    op.drop_index("ix_organizacoes_billing_subscription_id", table_name="organizacoes")
    op.drop_column("organizacoes", "billing_intervalo")
    op.drop_column("organizacoes", "billing_subscription_id")
    op.drop_column("planos_saas", "stripe_price_anual_id")
    op.drop_column("planos_saas", "stripe_price_mensal_id")

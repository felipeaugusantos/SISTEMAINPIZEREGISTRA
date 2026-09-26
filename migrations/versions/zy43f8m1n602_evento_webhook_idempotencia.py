"""Distingue eventos sucessivos da mesma cobrança financeira.

Revision ID: zy43f8m1n602
Revises: za44b5c6d7e8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zy43f8m1n602"
down_revision: str | None = "za44b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("webhooks_financeiros", sa.Column("chave_idempotencia", sa.String(length=255), nullable=True))
    op.execute("UPDATE webhooks_financeiros SET chave_idempotencia = 'legacy:' || id::text")
    op.alter_column("webhooks_financeiros", "chave_idempotencia", nullable=False)
    op.drop_constraint("uq_webhook_financeiro_org_referencia", "webhooks_financeiros", type_="unique")
    op.create_unique_constraint(
        "uq_webhook_financeiro_org_event_key",
        "webhooks_financeiros",
        ["organizacao_id", "chave_idempotencia"],
    )


def downgrade() -> None:
    # O schema antigo só permite uma linha por cobrança. Não perder eventos
    # posteriores silenciosamente: o downgrade exige ausência de duplicatas.
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM webhooks_financeiros
            GROUP BY organizacao_id, referencia HAVING COUNT(*) > 1
        ) THEN
            RAISE EXCEPTION 'Não é possível reverter: há múltiplos eventos para a mesma cobrança';
        END IF;
        END $$;"""
    )
    op.drop_constraint("uq_webhook_financeiro_org_event_key", "webhooks_financeiros", type_="unique")
    op.create_unique_constraint(
        "uq_webhook_financeiro_org_referencia",
        "webhooks_financeiros",
        ["organizacao_id", "referencia"],
    )
    op.drop_column("webhooks_financeiros", "chave_idempotencia")

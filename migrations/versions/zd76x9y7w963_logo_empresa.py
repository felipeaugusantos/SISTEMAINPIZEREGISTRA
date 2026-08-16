"""define a logo institucional da organização padrão

Revision ID: zd76x9y7w963
Revises: zc65w8x6v852
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zd76x9y7w963"
down_revision: str | None = "zc65w8x6v852"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE organizacoes
        SET branding = branding::jsonb || '{"logo_url":"/static/assets/logo-zeregistra.png"}'::jsonb
        WHERE slug = 'ze-registra'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE organizacoes
        SET branding = branding::jsonb || '{"logo_url":""}'::jsonb
        WHERE slug = 'ze-registra'
        """
    )

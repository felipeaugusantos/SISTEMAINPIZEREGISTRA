"""add trigram search indexes

Revision ID: b4d39e0812c7
Revises: 6a2c9bd4f701
Create Date: 2026-07-15 23:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4d39e0812c7"
down_revision: str | None = "6a2c9bd4f701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION immutable_unaccent(text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE PARALLEL SAFE STRICT
        AS $$ SELECT public.unaccent('public.unaccent', $1) $$
        """
    )
    op.execute(
        """
        CREATE INDEX ix_processos_titulo_trgm
        ON processos USING gin (immutable_unaccent(titulo) gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_titulares_nome_trgm
        ON titulares USING gin (immutable_unaccent(nome) gin_trgm_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_titulares_nome_trgm")
    op.execute("DROP INDEX IF EXISTS ix_processos_titulo_trgm")
    op.execute("DROP FUNCTION IF EXISTS immutable_unaccent(text)")

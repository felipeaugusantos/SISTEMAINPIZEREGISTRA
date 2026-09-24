"""remove campos sem produtor ou consumidor na prospeccao

Revision ID: g9b0c1d2e3f4
Revises: f8a9b0c1d2e3

Fase 16.4 (24/09/2026): endereco e dados_brutos nunca foram preenchidos
em Prospect; configuracao e ativo nunca foram lidos em ProspectFonte.
A origem continua identificada por tipo/nome, sem manter contratos falsos.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g9b0c1d2e3f4"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM prospects
                WHERE endereco IS NOT NULL
                   OR COALESCE(dados_brutos::jsonb, '{}'::jsonb) <> '{}'::jsonb
            ) THEN
                RAISE EXCEPTION 'campos mortos de prospects passaram a conter dados; revise antes de remover';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM prospect_fontes
                WHERE COALESCE(configuracao::jsonb, '{}'::jsonb) <> '{}'::jsonb
                   OR ativo IS FALSE
            ) THEN
                RAISE EXCEPTION 'campos mortos de prospect_fontes passaram a conter dados; revise antes de remover';
            END IF;
        END
        $$
        """
    )
    op.drop_column("prospects", "dados_brutos")
    op.drop_column("prospects", "endereco")
    op.drop_column("prospect_fontes", "configuracao")
    op.drop_column("prospect_fontes", "ativo")


def downgrade() -> None:
    op.add_column("prospect_fontes", sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("prospect_fontes", sa.Column("configuracao", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("prospects", sa.Column("endereco", sa.JSON(), nullable=True))
    op.add_column("prospects", sa.Column("dados_brutos", sa.JSON(), nullable=False, server_default="{}"))

"""link proposals to the originating trademark research

Revision ID: b27c4e8d9f10
Revises: ace88w3p1z85
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b27c4e8d9f10"
down_revision: str | None = "ace88w3p1z85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "propostas_comerciais",
        sa.Column("pesquisa_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_propostas_comerciais_pesquisa_id",
        "propostas_comerciais",
        ["pesquisa_id"],
    )
    op.create_foreign_key(
        "fk_proposta_pesquisa",
        "propostas_comerciais",
        "pesquisas_marca",
        ["pesquisa_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Preserva propostas antigas e associa cada uma à pesquisa mais recente do lead.
    op.execute(
        """
        UPDATE propostas_comerciais AS p
        SET pesquisa_id = (
            SELECT pm.id
            FROM pesquisas_marca AS pm
            WHERE pm.lead_id = p.lead_id
              AND pm.organizacao_id = p.organizacao_id
            ORDER BY pm.criado_em DESC, pm.id DESC
            LIMIT 1
        )
        WHERE p.pesquisa_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM pesquisas_marca AS pm2
              WHERE pm2.lead_id = p.lead_id
                AND pm2.organizacao_id = p.organizacao_id
          )
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_proposta_pesquisa", "propostas_comerciais", type_="foreignkey")
    op.drop_index("ix_propostas_comerciais_pesquisa_id", table_name="propostas_comerciais")
    op.drop_column("propostas_comerciais", "pesquisa_id")

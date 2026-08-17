"""adiciona vínculos faltantes em clientes_portal (processo/proposta/lançamento)

Revision ID: zx01p0rtalfks
Revises: j07e2f4567a1

Correção pontual: o modelo ClientePortal declara processo_id/proposta_id/
lancamento_id, mas as colunas não existiam no banco, causando
UndefinedColumnError (500) ao consultar o acesso do cliente. Adiciona só essas
três colunas (nullable, FK SET NULL) — aditivo e sem perda de dados.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zx01p0rtalfks"
down_revision: str | None = "j07e2f4567a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("clientes_portal", sa.Column("processo_id", sa.BigInteger(), nullable=True))
    op.add_column("clientes_portal", sa.Column("proposta_id", sa.BigInteger(), nullable=True))
    op.add_column("clientes_portal", sa.Column("lancamento_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_clientes_portal_processo_id", "clientes_portal", ["processo_id"])
    op.create_index("ix_clientes_portal_proposta_id", "clientes_portal", ["proposta_id"])
    op.create_index("ix_clientes_portal_lancamento_id", "clientes_portal", ["lancamento_id"])
    op.create_foreign_key(
        "fk_clientes_portal_processo", "clientes_portal", "processos",
        ["processo_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_clientes_portal_proposta", "clientes_portal", "propostas_comerciais",
        ["proposta_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_clientes_portal_lancamento", "clientes_portal", "lancamentos_financeiros",
        ["lancamento_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_clientes_portal_lancamento", "clientes_portal", type_="foreignkey")
    op.drop_constraint("fk_clientes_portal_proposta", "clientes_portal", type_="foreignkey")
    op.drop_constraint("fk_clientes_portal_processo", "clientes_portal", type_="foreignkey")
    op.drop_index("ix_clientes_portal_lancamento_id", table_name="clientes_portal")
    op.drop_index("ix_clientes_portal_proposta_id", table_name="clientes_portal")
    op.drop_index("ix_clientes_portal_processo_id", table_name="clientes_portal")
    op.drop_column("clientes_portal", "lancamento_id")
    op.drop_column("clientes_portal", "proposta_id")
    op.drop_column("clientes_portal", "processo_id")

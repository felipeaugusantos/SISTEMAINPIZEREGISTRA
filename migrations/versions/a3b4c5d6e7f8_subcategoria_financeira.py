"""subcategoria financeira

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7

Pedido do usuário (22/09/2026): tela de categorias financeiras com
suporte a subcategoria. categorias_financeiras ganha categoria_pai_id
(auto-referência, nullable -- categoria raiz continua com o valor
None). Profundidade limitada a 1 nível (uma subcategoria não pode ter
suas próprias subcategorias) -- validado em app/api/financeiro.py, não
no banco.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "categorias_financeiras",
        sa.Column("categoria_pai_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_categoria_financeira_pai",
        "categorias_financeiras",
        "categorias_financeiras",
        ["categoria_pai_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_categorias_financeiras_categoria_pai_id",
        "categorias_financeiras",
        ["categoria_pai_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_categorias_financeiras_categoria_pai_id", table_name="categorias_financeiras")
    op.drop_constraint("fk_categoria_financeira_pai", "categorias_financeiras", type_="foreignkey")
    op.drop_column("categorias_financeiras", "categoria_pai_id")

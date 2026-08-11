"""alinha indices do financeiro e das exclusoes

Revision ID: zd30e3b9c286
Revises: zc29d2a8b175
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zd30e3b9c286"
down_revision: str | None = "zc29d2a8b175"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_lancamentos_financeiros_descricao",
        "lancamentos_financeiros",
        ["descricao"],
    )
    op.create_index(
        "ix_lancamentos_financeiros_documento",
        "lancamentos_financeiros",
        ["documento"],
    )
    op.create_index(
        "ix_solicitacoes_exclusao_pesquisa_decidido_por_id",
        "solicitacoes_exclusao_pesquisa",
        ["decidido_por_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solicitacoes_exclusao_pesquisa_decidido_por_id",
        table_name="solicitacoes_exclusao_pesquisa",
    )
    op.drop_index(
        "ix_lancamentos_financeiros_documento",
        table_name="lancamentos_financeiros",
    )
    op.drop_index(
        "ix_lancamentos_financeiros_descricao",
        table_name="lancamentos_financeiros",
    )

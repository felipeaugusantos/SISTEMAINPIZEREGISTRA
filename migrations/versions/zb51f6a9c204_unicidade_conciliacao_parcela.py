"""Impede conciliacao de multiplas transacoes bancarias com a mesma parcela.

Revision ID: zb51f6a9c204
Revises: zy43f8m1n602
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zb51f6a9c204"
down_revision: str | None = "zy43f8m1n602"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_transacoes_bancarias_parcela_conciliada"


def upgrade() -> None:
    # Nao escolher automaticamente qual conciliacao manter: isso exige revisao
    # operacional. Falhar antes do DDL preserva os dados e explica como seguir.
    op.execute(
        sa.text(
            "DO $migration$ "
            "DECLARE grupos_duplicados bigint; "
            "BEGIN "
            "SELECT count(*) INTO grupos_duplicados FROM ("
            "SELECT parcela_id FROM transacoes_bancarias "
            "WHERE status = 'conciliada' AND parcela_id IS NOT NULL "
            "GROUP BY parcela_id HAVING count(*) > 1"
            ") AS duplicadas; "
            "IF grupos_duplicados > 0 THEN "
            "RAISE EXCEPTION 'Migration cancelada: % grupos de parcelas possuem mais de uma transacao conciliada. Revise manualmente os vinculos; nenhum dado foi alterado.', grupos_duplicados; "
            "END IF; "
            "END; $migration$"
        )
    )

    op.create_index(
        INDEX_NAME,
        "transacoes_bancarias",
        ["parcela_id"],
        unique=True,
        postgresql_where=sa.text("status = 'conciliada' AND parcela_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="transacoes_bancarias")

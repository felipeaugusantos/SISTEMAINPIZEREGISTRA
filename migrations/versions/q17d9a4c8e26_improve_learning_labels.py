"""improve supervised-learning labels and review evidence

Revision ID: q17d9a4c8e26
Revises: p16c8f2a7d13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q17d9a4c8e26"
down_revision: str | None = "p16c8f2a7d13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rotulos_historicos_marca", sa.Column("data_decisao", sa.Date(), nullable=True)
    )
    op.add_column(
        "rotulos_historicos_marca",
        sa.Column("tipo_decisao", sa.String(length=30), server_default="merito", nullable=False),
    )
    op.add_column(
        "rotulos_historicos_marca",
        sa.Column(
            "elegivel_treinamento", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )
    op.add_column(
        "rotulos_historicos_marca",
        sa.Column("motivo_inelegibilidade", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "rotulos_historicos_marca",
        sa.Column(
            "classificador_versao",
            sa.String(length=30),
            server_default="rotulo-marcario-1.0",
            nullable=False,
        ),
    )
    op.add_column(
        "rotulos_historicos_marca",
        sa.Column(
            "evidencias_classificacao",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_rotulos_historicos_marca_data_decisao",
        "rotulos_historicos_marca",
        ["data_decisao"],
    )
    op.create_index(
        "ix_rotulos_historicos_marca_tipo_decisao",
        "rotulos_historicos_marca",
        ["tipo_decisao"],
    )
    op.create_index(
        "ix_rotulos_historicos_marca_elegivel_treinamento",
        "rotulos_historicos_marca",
        ["elegivel_treinamento"],
    )
    op.create_index(
        "ix_rotulos_historicos_marca_classificador_versao",
        "rotulos_historicos_marca",
        ["classificador_versao"],
    )
    op.execute(
        """
        UPDATE rotulos_historicos_marca AS r
        SET data_decisao = m.data_rpi
        FROM movimentacoes AS m
        WHERE m.processo_id = r.processo_id
          AND m.numero_rpi = r.numero_rpi
          AND COALESCE(m.codigo_despacho, '') = COALESCE(r.despacho_codigo, '')
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rotulos_historicos_marca_classificador_versao",
        table_name="rotulos_historicos_marca",
    )
    op.drop_index(
        "ix_rotulos_historicos_marca_elegivel_treinamento",
        table_name="rotulos_historicos_marca",
    )
    op.drop_index(
        "ix_rotulos_historicos_marca_tipo_decisao",
        table_name="rotulos_historicos_marca",
    )
    op.drop_index(
        "ix_rotulos_historicos_marca_data_decisao",
        table_name="rotulos_historicos_marca",
    )
    for column in (
        "evidencias_classificacao",
        "classificador_versao",
        "motivo_inelegibilidade",
        "elegivel_treinamento",
        "tipo_decisao",
        "data_decisao",
    ):
        op.drop_column("rotulos_historicos_marca", column)

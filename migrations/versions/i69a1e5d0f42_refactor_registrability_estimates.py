"""refactor registrability estimates with uncertainty and governance

Revision ID: i69a1e5d0f42
Revises: h58f0d4c9e31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i69a1e5d0f42"
down_revision: str | None = "h58f0d4c9e31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "previsoes_registrabilidade", sa.Column("probabilidade_inferior", sa.Float(), nullable=True)
    )
    op.add_column(
        "previsoes_registrabilidade", sa.Column("probabilidade_superior", sa.Float(), nullable=True)
    )
    op.add_column(
        "previsoes_registrabilidade",
        sa.Column("confianca_rotulo", sa.String(20), nullable=False, server_default="baixa"),
    )
    op.add_column(
        "previsoes_registrabilidade",
        sa.Column("cobertura_entrada", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "previsoes_registrabilidade",
        sa.Column("elegivel_cliente", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "previsoes_registrabilidade",
        sa.Column("motivos_inelegibilidade", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "previsoes_registrabilidade",
        sa.Column(
            "escopo_estimativa",
            sa.String(50),
            nullable=False,
            server_default="deferimento_exame_merito",
        ),
    )
    op.add_column(
        "previsoes_registrabilidade",
        sa.Column("amostras_referencia", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("previsoes_registrabilidade", sa.Column("corte_dados", sa.Date(), nullable=True))
    op.create_index(
        "ix_previsoes_registrabilidade_elegivel_cliente",
        "previsoes_registrabilidade",
        ["elegivel_cliente"],
    )

    op.add_column(
        "controle_aprendizado_marca",
        sa.Column("maximo_ece", sa.Float(), nullable=False, server_default="0.12"),
    )
    op.add_column(
        "controle_aprendizado_marca",
        sa.Column("minimo_amostras_modelo", sa.Integer(), nullable=False, server_default="300"),
    )
    op.add_column(
        "controle_aprendizado_marca",
        sa.Column("minimo_amostras_teste", sa.Integer(), nullable=False, server_default="50"),
    )
    op.add_column(
        "controle_aprendizado_marca",
        sa.Column("largura_maxima_intervalo", sa.Float(), nullable=False, server_default="0.35"),
    )
    op.add_column(
        "controle_aprendizado_marca",
        sa.Column("minima_cobertura", sa.Float(), nullable=False, server_default="0.40"),
    )


def downgrade() -> None:
    for coluna in (
        "minima_cobertura",
        "largura_maxima_intervalo",
        "minimo_amostras_teste",
        "minimo_amostras_modelo",
        "maximo_ece",
    ):
        op.drop_column("controle_aprendizado_marca", coluna)
    op.drop_index(
        "ix_previsoes_registrabilidade_elegivel_cliente", table_name="previsoes_registrabilidade"
    )
    for coluna in (
        "corte_dados",
        "amostras_referencia",
        "escopo_estimativa",
        "motivos_inelegibilidade",
        "elegivel_cliente",
        "cobertura_entrada",
        "confianca_rotulo",
        "probabilidade_superior",
        "probabilidade_inferior",
    ):
        op.drop_column("previsoes_registrabilidade", coluna)

"""add supervised trademark learning

Revision ID: b82e6f4c9a10
Revises: a61f37c9d2e4
Create Date: 2026-08-06 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b82e6f4c9a10"
down_revision: str | Sequence[str] | None = "a61f37c9d2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rotulos_historicos_marca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("processo_id", sa.BigInteger(), nullable=False),
        sa.Column("rotulo", sa.String(30), nullable=False),
        sa.Column("alvo_deferimento", sa.Boolean(), nullable=False),
        sa.Column("fundamento", sa.String(50), nullable=False),
        sa.Column("origem", sa.String(30), nullable=False, server_default="rpi_automatica"),
        sa.Column("confianca", sa.Float(), nullable=False, server_default="1"),
        sa.Column("data_referencia", sa.Date(), nullable=False),
        sa.Column("numero_rpi", sa.Integer(), nullable=True),
        sa.Column("despacho_codigo", sa.String(30), nullable=True),
        sa.Column("despacho_descricao", sa.Text(), nullable=True),
        sa.Column("status_revisao", sa.String(20), nullable=False, server_default="pendente"),
        sa.Column("revisor", sa.String(150), nullable=True),
        sa.Column("observacoes_revisao", sa.Text(), nullable=True),
        sa.Column("revisado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["processo_id"], ["processos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rotulos_historicos_marca_processo_id",
        "rotulos_historicos_marca",
        ["processo_id"],
        unique=True,
    )
    for coluna in (
        "rotulo",
        "alvo_deferimento",
        "fundamento",
        "origem",
        "data_referencia",
        "numero_rpi",
        "status_revisao",
    ):
        op.create_index(
            f"ix_rotulos_historicos_marca_{coluna}", "rotulos_historicos_marca", [coluna]
        )

    op.create_table(
        "pares_treinamento_marca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("rotulo_id", sa.BigInteger(), nullable=False),
        sa.Column("processo_candidato_id", sa.BigInteger(), nullable=False),
        sa.Column("atributos", sa.JSON(), nullable=False),
        sa.Column("alvo_conflito", sa.Boolean(), nullable=True),
        sa.Column("origem", sa.String(30), nullable=False, server_default="candidato_temporal"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["rotulo_id"], ["rotulos_historicos_marca.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["processo_candidato_id"], ["processos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rotulo_id", "processo_candidato_id", name="uq_par_treinamento_rotulo_candidato"
        ),
    )
    op.create_index(
        "ix_pares_treinamento_marca_rotulo_id", "pares_treinamento_marca", ["rotulo_id"]
    )
    op.create_index(
        "ix_pares_treinamento_marca_processo_candidato_id",
        "pares_treinamento_marca",
        ["processo_candidato_id"],
    )
    op.create_index(
        "ix_pares_treinamento_marca_alvo_conflito", "pares_treinamento_marca", ["alvo_conflito"]
    )

    op.create_table(
        "modelos_registrabilidade",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("versao", sa.String(60), nullable=False),
        sa.Column("algoritmo", sa.String(50), nullable=False, server_default="regressao_logistica"),
        sa.Column("status", sa.String(20), nullable=False, server_default="candidato"),
        sa.Column("atributos", sa.JSON(), nullable=False),
        sa.Column("parametros", sa.JSON(), nullable=False),
        sa.Column("calibracao", sa.JSON(), nullable=False),
        sa.Column("metricas", sa.JSON(), nullable=False),
        sa.Column("dataset", sa.JSON(), nullable=False),
        sa.Column("corte_treino", sa.Date(), nullable=True),
        sa.Column("corte_validacao", sa.Date(), nullable=True),
        sa.Column("treinado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ativado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ativado_por", sa.String(150), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_modelos_registrabilidade_versao", "modelos_registrabilidade", ["versao"], unique=True
    )
    op.create_index("ix_modelos_registrabilidade_status", "modelos_registrabilidade", ["status"])
    op.create_index(
        "ix_modelos_registrabilidade_treinado_em", "modelos_registrabilidade", ["treinado_em"]
    )

    op.create_table(
        "previsoes_registrabilidade",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pesquisa_id", sa.String(36), nullable=False),
        sa.Column("modelo_id", sa.BigInteger(), nullable=False),
        sa.Column("modo", sa.String(20), nullable=False, server_default="sombra"),
        sa.Column("probabilidade_deferimento", sa.Float(), nullable=False),
        sa.Column("nivel", sa.String(30), nullable=False),
        sa.Column("confianca", sa.Float(), nullable=False),
        sa.Column("atributos", sa.JSON(), nullable=False),
        sa.Column("fatores_principais", sa.JSON(), nullable=False),
        sa.Column("nivel_humano", sa.String(30), nullable=True),
        sa.Column("avaliador", sa.String(150), nullable=True),
        sa.Column("observacoes_humanas", sa.Text(), nullable=True),
        sa.Column("avaliado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calculado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["pesquisa_id"], ["pesquisas_marca.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["modelo_id"], ["modelos_registrabilidade.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pesquisa_id", "modelo_id", name="uq_previsao_registrabilidade_pesquisa_modelo"
        ),
    )
    for coluna in ("pesquisa_id", "modelo_id", "modo", "nivel", "nivel_humano"):
        op.create_index(
            f"ix_previsoes_registrabilidade_{coluna}", "previsoes_registrabilidade", [coluna]
        )

    op.create_table(
        "controle_aprendizado_marca",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inferencia_habilitada", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rollout_percentual", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("exibir_cliente", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("minimo_revisoes_humanas", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("minimo_recall", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("maximo_brier", sa.Float(), nullable=False, server_default="0.25"),
        sa.Column("atualizado_por", sa.String(150), nullable=True),
        sa.Column("justificativa", sa.Text(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(sa.text("INSERT INTO controle_aprendizado_marca (id) VALUES (1)"))

    op.create_table(
        "execucoes_aprendizado_marca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("solicitado_por", sa.String(150), nullable=True),
        sa.Column("rotulos_processados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pares_processados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("modelo_id", sa.BigInteger(), nullable=True),
        sa.Column("metricas", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("mensagem", sa.Text(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["modelo_id"], ["modelos_registrabilidade.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_execucoes_aprendizado_marca_tipo", "execucoes_aprendizado_marca", ["tipo"])
    op.create_index(
        "ix_execucoes_aprendizado_marca_status", "execucoes_aprendizado_marca", ["status"]
    )


def downgrade() -> None:
    op.drop_table("execucoes_aprendizado_marca")
    op.drop_table("controle_aprendizado_marca")
    op.drop_table("previsoes_registrabilidade")
    op.drop_table("modelos_registrabilidade")
    op.drop_table("pares_treinamento_marca")
    op.drop_table("rotulos_historicos_marca")

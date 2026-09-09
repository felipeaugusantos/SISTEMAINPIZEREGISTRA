"""Fase 5: liberacao gradual de feature flags por grupo de implantacao

Revision ID: p42d8f6r286
Revises: l33v8x4s175

Adiciona a feature_flags um "dial" fino de audiencia (estagio_rollout),
usado somente quando estado_padrao == 'ligado' (Fase 4): sem isso, o
comportamento de quem ja usa a flag hoje continua identico (estagio
padrao 'liberacao_geral' == "ligado para todo mundo", como sempre foi).

Estagios, cumulativos (cada um inclui as audiencias dos anteriores):
1. ambiente_interno    -- so a(s) organizacao(oes) marcada(s) como interna
2. administradores     -- + usuarios administrador/superadmin de qualquer org
3. organizacoes_piloto -- na pratica ja e o mecanismo existente de override
                          por organizacao (feature_flags_organizacoes);
                          este estagio e sobretudo documentacional/ordinal
4. percentual_limitado -- + percentual das organizacoes (bucket estavel por
                          hash de codigo+organizacao_id, nao "pisca")
5. liberacao_geral     -- todo mundo (comportamento de hoje)

feature_flags_eventos: telemetria de uso/erro por grupo, para o
monitoramento por grupo (erros, tempo de resposta, uso). Tabela global de
observabilidade, sem RLS -- mesmo padrao de eventos_operacionais.

limite_taxa_erro + limite_eventos_minimo: circuito de interrupcao
automatica (ver app.feature_flags.avaliar_circuito_flags, rodado
periodicamente pelo worker). Estourar o limite recua um estagio (ou
desativa, se ja estiver no estagio minimo) sem derrubar o sistema --
critério de aceite da Fase 5.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p42d8f6r286"
down_revision: str | None = "l33v8x4s175"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ESTAGIOS = "('ambiente_interno', 'administradores', 'organizacoes_piloto', 'percentual_limitado', 'liberacao_geral')"


def upgrade() -> None:
    op.add_column(
        "organizacoes",
        sa.Column("ambiente_interno", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.add_column(
        "feature_flags",
        sa.Column("estagio_rollout", sa.String(30), nullable=False, server_default="liberacao_geral"),
    )
    op.add_column(
        "feature_flags",
        sa.Column("percentual_rollout", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column("feature_flags", sa.Column("limite_taxa_erro", sa.Float(), nullable=True))
    op.add_column(
        "feature_flags",
        sa.Column("limite_eventos_minimo", sa.Integer(), nullable=False, server_default="20"),
    )
    op.add_column("feature_flags", sa.Column("pausado_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("feature_flags", sa.Column("pausado_motivo", sa.Text(), nullable=True))
    op.add_column("feature_flags", sa.Column("pausado_por", sa.String(254), nullable=True))
    op.create_check_constraint(
        "ck_feature_flag_estagio_rollout", "feature_flags", f"estagio_rollout IN {ESTAGIOS}"
    )
    op.create_check_constraint(
        "ck_feature_flag_percentual_rollout",
        "feature_flags",
        "percentual_rollout >= 0 AND percentual_rollout <= 100",
    )
    op.create_check_constraint(
        "ck_feature_flag_limite_taxa_erro",
        "feature_flags",
        "limite_taxa_erro IS NULL OR (limite_taxa_erro > 0 AND limite_taxa_erro <= 1)",
    )

    op.create_table(
        "feature_flags_eventos",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "feature_flag_id",
            sa.BigInteger(),
            sa.ForeignKey("feature_flags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("grupo", sa.String(30), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("duracao_ms", sa.Integer(), nullable=True),
        sa.Column("detalhes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"grupo IN {ESTAGIOS}", name="ck_feature_flag_evento_grupo"),
        sa.CheckConstraint(
            "tipo IN ('uso', 'erro', 'falha_integracao')", name="ck_feature_flag_evento_tipo"
        ),
    )
    op.create_index("ix_feature_flags_eventos_feature_flag_id", "feature_flags_eventos", ["feature_flag_id"])
    op.create_index("ix_feature_flags_eventos_organizacao_id", "feature_flags_eventos", ["organizacao_id"])
    op.create_index("ix_feature_flags_eventos_tipo", "feature_flags_eventos", ["tipo"])
    op.create_index("ix_feature_flags_eventos_criado_em", "feature_flags_eventos", ["criado_em"])
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "feature_flags_eventos" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("feature_flags_eventos")
    op.drop_constraint("ck_feature_flag_limite_taxa_erro", "feature_flags", type_="check")
    op.drop_constraint("ck_feature_flag_percentual_rollout", "feature_flags", type_="check")
    op.drop_constraint("ck_feature_flag_estagio_rollout", "feature_flags", type_="check")
    op.drop_column("feature_flags", "pausado_por")
    op.drop_column("feature_flags", "pausado_motivo")
    op.drop_column("feature_flags", "pausado_em")
    op.drop_column("feature_flags", "limite_eventos_minimo")
    op.drop_column("feature_flags", "limite_taxa_erro")
    op.drop_column("feature_flags", "percentual_rollout")
    op.drop_column("feature_flags", "estagio_rollout")
    op.drop_column("organizacoes", "ambiente_interno")

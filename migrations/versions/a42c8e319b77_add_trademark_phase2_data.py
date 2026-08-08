"""add trademark phase 2 data

Revision ID: a42c8e319b77
Revises: f18d2a794c61
Create Date: 2026-07-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a42c8e319b77"
down_revision: str | Sequence[str] | None = "f18d2a794c61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MATRIZ_INICIAL = (
    (
        "03",
        "05",
        "alta",
        "Cosméticos e preparações farmacêuticas podem compartilhar finalidade e público.",
    ),
    ("05", "10", "alta", "Preparações médicas e aparelhos médicos atuam em mercados próximos."),
    (
        "09",
        "38",
        "moderada",
        "Eletrônicos e software podem estar ligados a serviços de telecomunicação.",
    ),
    (
        "09",
        "42",
        "alta",
        "Software como produto e desenvolvimento tecnológico têm forte proximidade.",
    ),
    ("16", "41", "moderada", "Materiais impressos podem estar ligados a educação e treinamento."),
    ("18", "25", "moderada", "Bolsas e artigos de couro podem compartilhar canais com vestuário."),
    ("25", "35", "alta", "Vestuário e serviços de varejo de roupas compartilham público e canais."),
    (
        "29",
        "30",
        "alta",
        "As duas classes abrangem alimentos frequentemente comercializados juntos.",
    ),
    (
        "30",
        "32",
        "moderada",
        "Alimentos e bebidas não alcoólicas podem compartilhar origem e canais.",
    ),
    ("32", "33", "alta", "Bebidas alcoólicas e não alcoólicas compartilham natureza e canais."),
    (
        "35",
        "39",
        "moderada",
        "Comércio eletrônico e entrega/logística podem compor o mesmo serviço.",
    ),
    (
        "35",
        "42",
        "moderada",
        "Gestão de negócios digitais pode estar ligada a serviços tecnológicos.",
    ),
    (
        "36",
        "42",
        "moderada",
        "Serviços financeiros digitais podem depender de tecnologia especializada.",
    ),
    ("37", "42", "moderada", "Instalação técnica e engenharia podem compartilhar finalidade."),
    (
        "41",
        "42",
        "moderada",
        "Educação digital e plataformas tecnológicas podem compartilhar público.",
    ),
    (
        "30",
        "43",
        "alta",
        "Alimentos e serviços de alimentação podem compartilhar origem percebida.",
    ),
    ("32", "43", "alta", "Bebidas e serviços de alimentação podem compartilhar canais e público."),
    ("05", "44", "alta", "Produtos de saúde e serviços médicos podem compartilhar finalidade."),
    ("10", "44", "alta", "Aparelhos médicos e serviços de saúde têm forte proximidade."),
)


def upgrade() -> None:
    op.add_column(
        "processos", sa.Column("situacao_normalizada", sa.String(length=30), nullable=True)
    )
    op.add_column(
        "processos", sa.Column("relevancia_situacao", sa.String(length=20), nullable=True)
    )
    op.create_index(
        "ix_processos_situacao_normalizada",
        "processos",
        ["situacao_normalizada"],
        unique=False,
    )
    op.create_index(
        "ix_processos_relevancia_situacao",
        "processos",
        ["relevancia_situacao"],
        unique=False,
    )

    op.create_table(
        "marcas_alto_renome",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("numero_processo_normalizado", sa.String(length=30), nullable=False),
        sa.Column("vigente", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fonte_url", sa.Text(), nullable=False),
        sa.Column("fonte_atualizada_em", sa.Date(), nullable=True),
        sa.Column(
            "sincronizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("numero_processo_normalizado"),
    )
    op.create_index(
        "ix_marcas_alto_renome_numero_processo_normalizado",
        "marcas_alto_renome",
        ["numero_processo_normalizado"],
        unique=True,
    )
    op.create_index(
        "ix_marcas_alto_renome_vigente",
        "marcas_alto_renome",
        ["vigente"],
        unique=False,
    )

    afinidades = op.create_table(
        "afinidades_classes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("classe_origem", sa.String(length=2), nullable=False),
        sa.Column("classe_destino", sa.String(length=2), nullable=False),
        sa.Column("nivel", sa.String(length=20), nullable=False),
        sa.Column("justificativa", sa.Text(), nullable=False),
        sa.Column(
            "versao",
            sa.String(length=20),
            nullable=False,
            server_default="inicial-2026",
        ),
        sa.Column(
            "status_revisao",
            sa.String(length=20),
            nullable=False,
            server_default="pendente",
        ),
        sa.Column("revisor", sa.String(length=150), nullable=True),
        sa.Column("observacoes_revisao", sa.Text(), nullable=True),
        sa.Column("revisado_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "classe_origem",
            "classe_destino",
            name="uq_afinidades_classes_par",
        ),
    )
    op.create_index(
        "ix_afinidades_classes_classe_origem",
        "afinidades_classes",
        ["classe_origem"],
        unique=False,
    )
    op.create_index(
        "ix_afinidades_classes_classe_destino",
        "afinidades_classes",
        ["classe_destino"],
        unique=False,
    )
    op.create_index(
        "ix_afinidades_classes_status_revisao",
        "afinidades_classes",
        ["status_revisao"],
        unique=False,
    )
    op.bulk_insert(
        afinidades,
        [
            {
                "classe_origem": origem,
                "classe_destino": destino,
                "nivel": nivel,
                "justificativa": justificativa,
                "versao": "inicial-2026",
                "status_revisao": "pendente",
            }
            for origem, destino, nivel, justificativa in MATRIZ_INICIAL
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_afinidades_classes_status_revisao", table_name="afinidades_classes")
    op.drop_index("ix_afinidades_classes_classe_destino", table_name="afinidades_classes")
    op.drop_index("ix_afinidades_classes_classe_origem", table_name="afinidades_classes")
    op.drop_table("afinidades_classes")
    op.drop_index("ix_marcas_alto_renome_vigente", table_name="marcas_alto_renome")
    op.drop_index(
        "ix_marcas_alto_renome_numero_processo_normalizado",
        table_name="marcas_alto_renome",
    )
    op.drop_table("marcas_alto_renome")
    op.drop_index("ix_processos_relevancia_situacao", table_name="processos")
    op.drop_index("ix_processos_situacao_normalizada", table_name="processos")
    op.drop_column("processos", "relevancia_situacao")
    op.drop_column("processos", "situacao_normalizada")

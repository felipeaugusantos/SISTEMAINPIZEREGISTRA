"""tabela de retribuições do INPI (serviços de marca) — referência global

Revision ID: zm99h4l0f286
Revises: zl88g3k9e175
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zm99h4l0f286"
down_revision: str | None = "zl88g3k9e175"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Catálogo dos serviços de marca mais comuns. Valores/códigos ficam em branco de
# propósito: o usuário confere e preenche uma vez contra a tabela oficial vigente
# (confirmado=false até lá), evitando cobrar em cima de valor de referência.
SEED = [
    ("deposito", "Depósito de pedido de registro de marca", "protocolo_inpi", 1),
    ("exigencia_exame", "Cumprimento de exigência (exame)", None, 2),
    ("oposicao", "Apresentação de oposição", None, 3),
    ("manifestacao_oposicao", "Manifestação à oposição", None, 4),
    ("concessao_1decenio", "Concessão do registro + 1º decênio", None, 5),
    ("prorrogacao", "Prorrogação do registro (decênio)", None, 6),
    ("prorrogacao_extraordinaria", "Prorrogação com sobretaxa (até 6 meses após)", None, 7),
    ("recurso", "Recurso", None, 8),
    ("nulidade", "Processo administrativo de nulidade (PAN)", None, 9),
    ("caducidade", "Requerimento de caducidade", None, 10),
    ("transferencia", "Anotação de transferência de titularidade", None, 11),
    ("alteracao", "Anotação de alteração (nome/endereço)", None, 12),
    ("segunda_via_certificado", "Segunda via de certificado de registro", None, 13),
]


def upgrade() -> None:
    op.create_table(
        "retribuicoes_inpi",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("servico", sa.String(60), nullable=False),
        sa.Column("descricao", sa.String(200), nullable=False),
        sa.Column("grupo", sa.String(20), server_default="marca", nullable=False),
        sa.Column("codigo", sa.String(10), nullable=True),
        sa.Column("valor_normal", sa.Numeric(12, 2), nullable=True),
        sa.Column("valor_reduzido", sa.Numeric(12, 2), nullable=True),
        sa.Column("fase_sugerida", sa.String(30), nullable=True),
        sa.Column("confirmado", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("servico", name="uq_retribuicao_inpi_servico"),
    )
    op.create_index("ix_retribuicoes_inpi_servico", "retribuicoes_inpi", ["servico"], unique=True)
    op.create_index("ix_retribuicoes_inpi_grupo", "retribuicoes_inpi", ["grupo"])
    op.create_index("ix_retribuicoes_inpi_codigo", "retribuicoes_inpi", ["codigo"])
    op.create_index("ix_retribuicoes_inpi_fase_sugerida", "retribuicoes_inpi", ["fase_sugerida"])
    op.create_index("ix_retribuicoes_inpi_ativo", "retribuicoes_inpi", ["ativo"])

    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "retribuicoes_inpi" TO inpi_app')

    tabela = sa.table(
        "retribuicoes_inpi",
        sa.column("servico", sa.String),
        sa.column("descricao", sa.String),
        sa.column("grupo", sa.String),
        sa.column("fase_sugerida", sa.String),
        sa.column("ordem", sa.Integer),
    )
    op.bulk_insert(
        tabela,
        [
            {"servico": s, "descricao": d, "grupo": "marca", "fase_sugerida": f, "ordem": o}
            for (s, d, f, o) in SEED
        ],
    )


def downgrade() -> None:
    op.drop_table("retribuicoes_inpi")

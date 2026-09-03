"""radar de prospeccao: historico de importacao do CNPJ (RFB) disparavel pela tela

Revision ID: prjnxiq9ajtm
Revises: dmehwpmz3gbi

O usuario pediu para disparar a importacao do cache de empresas (Fase 2 do
Radar) direto pela tela, com acompanhamento de progresso -- mesmo padrao ja
usado pela sincronizacao da RPI (RpiSyncExecucao/RpiSyncEstado): uma linha
por execucao, com etapa atual e contadores, atualizada em commits proprios
(nao so no fim) para o polling da tela enxergar o progresso em tempo real.

Sem RLS -- alimenta cache_estabelecimentos_rfb, que tambem nao e por tenant
(dado publico compartilhado entre organizacoes do SaaS). Disparo fica restrito
a superadmin (afeta a plataforma inteira, nao um tenant so).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "prjnxiq9ajtm"
down_revision: str | None = "dmehwpmz3gbi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "importacoes_cnpj_rfb",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="executando"),
        sa.Column("periodo", sa.String(length=7), nullable=True),
        sa.Column("etapa_atual", sa.String(length=200), nullable=True),
        sa.Column("total_processados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_validos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("solicitado_por", sa.String(length=150), nullable=True),
        sa.Column("solicitado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('executando', 'concluido', 'erro')", name="ck_importacoes_cnpj_rfb_status_valido"
        ),
    )
    op.create_index("ix_importacoes_cnpj_rfb_status", "importacoes_cnpj_rfb", ["status"])
    op.create_index("ix_importacoes_cnpj_rfb_solicitado_em", "importacoes_cnpj_rfb", ["solicitado_em"])


def downgrade() -> None:
    op.drop_table("importacoes_cnpj_rfb")

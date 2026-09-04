"""remove colunas orfas em guias_inpi (achado FASE6-2)

Revision ID: wn21o9q3b842
Revises: wm10n8p2a731

Achado FASE6-2 da auditoria (04/09/2026, alembic check): guias_inpi tem as
colunas processo_id, proposta_id e lancamento_id (com FK e índice cada) no
banco de produção, mas GuiaInpi (app/models.py) não as declara há tempo --
nenhum código no repositório lê ou escreve nelas (grep confirmado). GuiaInpi
hoje só se vincula a lead_id; o vínculo com processo/proposta/lançamento
aparentemente migrou para outras tabelas (contratacoes_servicos,
custos_juridicos) num refactor anterior que nunca teve a migração de
limpeza correspondente. Downgrade recria as colunas (sem dados -- eram
colunas mortas, não há o que restaurar).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "wn21o9q3b842"
down_revision: str | None = "wm10n8p2a731"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("fk_guia_lancamento", "guias_inpi", type_="foreignkey")
    op.drop_constraint("fk_guia_proposta", "guias_inpi", type_="foreignkey")
    op.drop_constraint("fk_guia_processo", "guias_inpi", type_="foreignkey")
    op.drop_index("ix_guias_inpi_lancamento_id", table_name="guias_inpi")
    op.drop_index("ix_guias_inpi_processo_id", table_name="guias_inpi")
    op.drop_index("ix_guias_inpi_proposta_id", table_name="guias_inpi")
    op.drop_column("guias_inpi", "lancamento_id")
    op.drop_column("guias_inpi", "proposta_id")
    op.drop_column("guias_inpi", "processo_id")


def downgrade() -> None:
    op.add_column("guias_inpi", sa.Column("processo_id", sa.BigInteger(), nullable=True))
    op.add_column("guias_inpi", sa.Column("proposta_id", sa.BigInteger(), nullable=True))
    op.add_column("guias_inpi", sa.Column("lancamento_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_guias_inpi_proposta_id", "guias_inpi", ["proposta_id"])
    op.create_index("ix_guias_inpi_processo_id", "guias_inpi", ["processo_id"])
    op.create_index("ix_guias_inpi_lancamento_id", "guias_inpi", ["lancamento_id"])
    op.create_foreign_key(
        "fk_guia_processo", "guias_inpi", "processos", ["processo_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_guia_proposta", "guias_inpi", "propostas_comerciais", ["proposta_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_guia_lancamento", "guias_inpi", "lancamentos_financeiros", ["lancamento_id"], ["id"], ondelete="SET NULL"
    )

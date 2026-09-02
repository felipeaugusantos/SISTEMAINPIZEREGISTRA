"""contratacao unica por proposta: servico opcional + unique em proposta_id

Revision ID: ej80k1l2m418
Revises: di79j0k1l307

Fase 4 do plano proposta-financeiro (03/09/2026), achado 5: nada impedia
duas contratacoes (ContratacaoServico/LancamentoFinanceiro) para a mesma
proposta -- o idempotency_key de POST /financeiro/contratacoes e' fornecido
pelo chamador, entao duas chamadas com chaves diferentes geravam cobranca
duplicada. Esta migration adiciona uma constraint de unicidade em
contratacoes_servicos.proposta_id (NULLs continuam distintos entre si no
Postgres, entao contratacoes sem proposta nao sao afetadas).

servico_id tambem passa a ser opcional: a contratacao automatica gerada no
aceite da proposta usa o valor ASSINADO (honorarios + taxa_gru), nao um
ServicoFinanceiro do catalogo -- nao ha item de catalogo para vincular.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ej80k1l2m418"
down_revision: str | None = "di79j0k1l307"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("contratacoes_servicos", "servico_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_unique_constraint(
        "uq_contratacao_servico_proposta", "contratacoes_servicos", ["proposta_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_contratacao_servico_proposta", "contratacoes_servicos", type_="unique")
    op.alter_column("contratacoes_servicos", "servico_id", existing_type=sa.BigInteger(), nullable=False)

"""adiciona status 'dispensado' aos prazos jurídicos

Marcas depositadas a partir de 20/09/2025 (unificação de retribuições do INPI)
não têm prazo de pagamento da concessão; o motor registra um prazo informativo
com status 'dispensado' no lugar do prazo acionável.

Revision ID: zi55d0h6b842
Revises: zh44c8f3g620
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zi55d0h6b842"
down_revision: str | None = "zh44c8f3g620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_ANTIGO = (
    "status IN ('aguardando_confirmacao','pendente','em_andamento',"
    "'concluido','cancelado')"
)
_STATUS_NOVO = (
    "status IN ('aguardando_confirmacao','pendente','em_andamento',"
    "'concluido','cancelado','dispensado')"
)


def upgrade() -> None:
    op.drop_constraint("ck_prazos_juridicos_status", "prazos_juridicos", type_="check")
    op.create_check_constraint(
        "ck_prazos_juridicos_status", "prazos_juridicos", _STATUS_NOVO
    )


def downgrade() -> None:
    op.drop_constraint("ck_prazos_juridicos_status", "prazos_juridicos", type_="check")
    op.create_check_constraint(
        "ck_prazos_juridicos_status", "prazos_juridicos", _STATUS_ANTIGO
    )

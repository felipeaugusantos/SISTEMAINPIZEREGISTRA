"""classifica prazos historicos e duplicados da RPI

Revision ID: zr44m9q5k731
Revises: zq33l8p4j620
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zr44m9q5k731"
down_revision: str | None = "zq33l8p4j620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_ANTIGO = (
    "status IN ('aguardando_confirmacao','pendente','em_andamento',"
    "'concluido','cancelado','dispensado')"
)
STATUS_NOVO = (
    "status IN ('aguardando_confirmacao','pendente','em_andamento',"
    "'concluido','cancelado','dispensado','historico','duplicado')"
)


def upgrade() -> None:
    op.drop_constraint("ck_prazos_juridicos_status", "prazos_juridicos", type_="check")
    op.create_check_constraint(
        "ck_prazos_juridicos_status", "prazos_juridicos", STATUS_NOVO
    )


def downgrade() -> None:
    op.execute(
        "UPDATE prazos_juridicos SET status = 'cancelado' "
        "WHERE status IN ('historico', 'duplicado')"
    )
    op.drop_constraint("ck_prazos_juridicos_status", "prazos_juridicos", type_="check")
    op.create_check_constraint(
        "ck_prazos_juridicos_status", "prazos_juridicos", STATUS_ANTIGO
    )

"""sla de primeiro atendimento: prazo configuravel por organizacao

Revision ID: dv08q4x0j720
Revises: cu97p3w9i619

Achado item 14 da auditoria completa do CRM (06/09/2026): so existia uma
media historica agregada de tempo ate o primeiro contato
(_tempo_medio_primeiro_atendimento_horas, app/api/leads.py), util para
relatorio gerencial mas sem alerta operacional individual por lead --
diferente do SLA de protocolo/proposta, que ja tem prazo e status
proprios. `horas_sla_primeiro_atendimento` nulo (padrao) mantem o
comportamento atual -- so ativa quando a organizacao definir um prazo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "dv08q4x0j720"
down_revision: str | None = "cu97p3w9i619"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "politicas_crm",
        sa.Column("horas_sla_primeiro_atendimento", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_politica_crm_horas_sla_primeiro_atendimento",
        "politicas_crm",
        "horas_sla_primeiro_atendimento IS NULL OR "
        "(horas_sla_primeiro_atendimento >= 1 AND horas_sla_primeiro_atendimento <= 720)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_politica_crm_horas_sla_primeiro_atendimento", "politicas_crm", type_="check")
    op.drop_column("politicas_crm", "horas_sla_primeiro_atendimento")

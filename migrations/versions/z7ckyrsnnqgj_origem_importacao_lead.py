"""adiciona 'importacao' as origens validas de leads

Revision ID: z7ckyrsnnqgj
Revises: jo35p8r9s984

Fase 4 do roadmap pos-auditoria de Leads (03/09/2026): importacao em massa de
leads via CSV/XLSX precisa de uma origem propria (distinta de "operador", que
ja significa lead aberto manualmente um a um por um atendente durante um
atendimento). ck_leads_origem_valida (criada em jo35p8r9s984) precisa incluir
o novo valor.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "z7ckyrsnnqgj"
down_revision: str | None = "jo35p8r9s984"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGENS_ANTIGAS = ("resultados", "processo", "geral", "landing", "operador", "relatorio")
ORIGENS_NOVAS = (*ORIGENS_ANTIGAS, "importacao")


def upgrade() -> None:
    op.drop_constraint("ck_leads_origem_valida", "leads", type_="check")
    origens_sql = ", ".join(f"'{o}'" for o in ORIGENS_NOVAS)
    op.create_check_constraint("ck_leads_origem_valida", "leads", f"origem IN ({origens_sql})")


def downgrade() -> None:
    op.drop_constraint("ck_leads_origem_valida", "leads", type_="check")
    origens_sql = ", ".join(f"'{o}'" for o in ORIGENS_ANTIGAS)
    op.create_check_constraint("ck_leads_origem_valida", "leads", f"origem IN ({origens_sql})")

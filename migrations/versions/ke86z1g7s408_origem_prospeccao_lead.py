"""adiciona 'prospeccao' as origens validas de leads

Revision ID: ke86z1g7s408
Revises: jd75y0f6r397

Achado do usuário (08/09/2026): "Converter em lead" no Radar de Prospecção
sempre falhava com 500 (IntegrityError -- CheckViolationError em
ck_leads_origem_valida) porque converter_prospect_em_lead
(app/api/prospeccao.py) grava origem="prospeccao" desde que o endpoint foi
criado, mas esse valor nunca foi incluído na constraint (criada em
jo35p8r9s984, ampliada em z7ckyrsnnqgj) -- bug pré-existente, nunca pego
pelos testes porque FakeSession não valida constraints reais do Postgres.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ke86z1g7s408"
down_revision: str | None = "jd75y0f6r397"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGENS_ANTIGAS = ("resultados", "processo", "geral", "landing", "operador", "relatorio", "importacao")
ORIGENS_NOVAS = (*ORIGENS_ANTIGAS, "prospeccao")


def upgrade() -> None:
    op.drop_constraint("ck_leads_origem_valida", "leads", type_="check")
    origens_sql = ", ".join(f"'{o}'" for o in ORIGENS_NOVAS)
    op.create_check_constraint("ck_leads_origem_valida", "leads", f"origem IN ({origens_sql})")


def downgrade() -> None:
    op.drop_constraint("ck_leads_origem_valida", "leads", type_="check")
    origens_sql = ", ".join(f"'{o}'" for o in ORIGENS_ANTIGAS)
    op.create_check_constraint("ck_leads_origem_valida", "leads", f"origem IN ({origens_sql})")

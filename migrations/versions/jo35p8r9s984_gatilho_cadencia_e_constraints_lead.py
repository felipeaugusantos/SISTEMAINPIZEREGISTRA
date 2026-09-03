"""gatilho automatico de cadencia + constraints de fase/origem em leads

Revision ID: jo35p8r9s984
Revises: in24o6p7q873

Fase 3 da auditoria de Leads (03/09/2026), achados P2:

1. cadencias ganha gatilho_evento/gatilho_valor (NULL = so aplicacao manual,
   comportamento de sempre). Quando preenchido, a cadencia passa a ser
   disparada sozinha pelo mesmo evento/valor de REGRAS_AUTOMACAO (ex.:
   status="sem_retorno").

2. leads.fase e leads.origem eram strings livres sem CHECK CONSTRAINT --
   uma escrita direta no banco podia gravar um valor fora do que o Python
   reconhece. Valores atuais de producao conferidos antes (so os 7 de
   ORDEM_FASE_LEAD e os 6 usados pelos endpoints de criacao), sem risco de
   quebrar dado existente.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "jo35p8r9s984"
down_revision: str | None = "in24o6p7q873"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FASES = (
    "contato_inicial",
    "relatorio_enviado",
    "proposta_enviada",
    "proposta_aceita",
    "pagamento_realizado",
    "protocolo_inpi",
    "processo_inpi",
)
ORIGENS = ("resultados", "processo", "geral", "landing", "operador", "relatorio")


def upgrade() -> None:
    op.add_column("cadencias", sa.Column("gatilho_evento", sa.String(length=20), nullable=True))
    op.add_column("cadencias", sa.Column("gatilho_valor", sa.String(length=30), nullable=True))
    op.create_index("ix_cadencias_gatilho_evento", "cadencias", ["gatilho_evento"])

    fases_sql = ", ".join(f"'{f}'" for f in FASES)
    origens_sql = ", ".join(f"'{o}'" for o in ORIGENS)
    op.create_check_constraint("ck_leads_fase_valida", "leads", f"fase IN ({fases_sql})")
    op.create_check_constraint("ck_leads_origem_valida", "leads", f"origem IN ({origens_sql})")


def downgrade() -> None:
    op.drop_constraint("ck_leads_origem_valida", "leads", type_="check")
    op.drop_constraint("ck_leads_fase_valida", "leads", type_="check")
    op.drop_index("ix_cadencias_gatilho_evento", table_name="cadencias")
    op.drop_column("cadencias", "gatilho_valor")
    op.drop_column("cadencias", "gatilho_evento")

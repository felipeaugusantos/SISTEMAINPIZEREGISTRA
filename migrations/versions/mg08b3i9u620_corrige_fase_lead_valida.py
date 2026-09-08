"""corrige ck_leads_fase_valida para as 10 fases atuais de FaseLead

Revision ID: mg08b3i9u620
Revises: lf97a2h8t519

Achado numa auditoria sistemática (08/09/2026, mesmo padrão dos bugs de
ck_leads_origem_valida, ck_prospect_triagens_classificacao_valida e
ck_prospects_triagem_marca_status_valido, todos corrigidos nesta sessão):
o enum FaseLead (app/models.py) foi expandido de 7 para 10 valores na
auditoria de 04/09/2026 (achado CRM-11) -- "qualificado" virou fase
própria, "pagamento_realizado" virou duas fases (aguardando_pagamento /
pagamento_confirmado) e "ganho" virou fase do funil -- mas a constraint
ck_leads_fase_valida (criada em jo35p8r9s984) nunca foi atualizada.

Isso deixa 4 valores do enum atual fora da constraint, usados em pontos
reais de produção: avancar_fase_lead grava "aguardando_pagamento"
(app/api/leads.py, fluxo de cobrança), "pagamento_confirmado"
(app/api/leads.py, fluxo financeiro) e "ganho" (app/api/leads.py e
app/api/juridico.py, fechamento de lead) -- qualquer uma dessas chamadas
deve falhar com CheckViolationError, igual aos 3 casos já corrigidos hoje.

Confirmado em produção: nenhum lead usa o valor antigo "pagamento_realizado"
(substituído pelas duas fases novas), então a migration pode remover esse
valor morto com segurança, sem quebrar dado existente.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "mg08b3i9u620"
down_revision: str | None = "lf97a2h8t519"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FASES_ANTIGAS = (
    "contato_inicial",
    "relatorio_enviado",
    "proposta_enviada",
    "proposta_aceita",
    "pagamento_realizado",
    "protocolo_inpi",
    "processo_inpi",
)
FASES_NOVAS = (
    "contato_inicial",
    "qualificado",
    "relatorio_enviado",
    "proposta_enviada",
    "proposta_aceita",
    "aguardando_pagamento",
    "pagamento_confirmado",
    "ganho",
    "protocolo_inpi",
    "processo_inpi",
)


def upgrade() -> None:
    op.drop_constraint("ck_leads_fase_valida", "leads", type_="check")
    fases_sql = ", ".join(f"'{f}'" for f in FASES_NOVAS)
    op.create_check_constraint("ck_leads_fase_valida", "leads", f"fase IN ({fases_sql})")


def downgrade() -> None:
    op.drop_constraint("ck_leads_fase_valida", "leads", type_="check")
    fases_sql = ", ".join(f"'{f}'" for f in FASES_ANTIGAS)
    op.create_check_constraint("ck_leads_fase_valida", "leads", f"fase IN ({fases_sql})")

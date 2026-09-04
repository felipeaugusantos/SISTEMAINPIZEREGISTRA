"""Expande FaseLead de 7 para 10 fases (achado CRM-11 da auditoria).

Achado CRM-11 (04/09/2026): o funil tinha 7 fases (contato_inicial,
relatorio_enviado, proposta_enviada, proposta_aceita, pagamento_realizado,
protocolo_inpi, processo_inpi) e conflava "proposta aceita" com "negócio
ganho" -- StatusLead.CONVERTIDO (e Lead.resultado="ganho") disparavam no
aceite da proposta, antes de qualquer pagamento. Expandido para 10 fases:
contato_inicial, qualificado, relatorio_enviado, proposta_enviada,
proposta_aceita, aguardando_pagamento, pagamento_confirmado, ganho,
protocolo_inpi, processo_inpi.

"contrato_assinado" NÃO virou uma fase própria (decisão de produto
documentada em app/models.py::FaseLead): neste sistema assinar a proposta
é o mesmo ato de aceitá-la.

Migração de dados (só remapeia rótulos de fase já existentes -- nunca
altera Lead.resultado nem avança nenhuma fase):
- "pagamento_realizado" -> "pagamento_confirmado" (era o rótulo mais
  próximo do estado real: pagamento já efetivado).
- Todas as outras fases mantêm o nome. "qualificado" e "ganho" são fases
  novas que só passam a ser usadas em transições futuras -- nenhum lead
  existente é movido retroativamente para elas (isso seria uma mudança
  automática de posição no funil, fora do escopo de uma migração de
  dados; ver app/crm.py::avancar_fase_lead, que só avança fases a partir
  de agora, nunca retrocede nem "corrige" o passado).
"""

from alembic import op

revision = "uw24qat2dmwp"
down_revision = "rt13pzs1clvo"
branch_labels = None
depends_on = None

_DE_PARA = ("pagamento_realizado", "pagamento_confirmado")


def upgrade() -> None:
    op.execute(f"UPDATE leads SET fase = '{_DE_PARA[1]}' WHERE fase = '{_DE_PARA[0]}'")
    op.execute(f"UPDATE historico_fase_lead SET fase = '{_DE_PARA[1]}' WHERE fase = '{_DE_PARA[0]}'")


def downgrade() -> None:
    op.execute(f"UPDATE leads SET fase = '{_DE_PARA[0]}' WHERE fase = '{_DE_PARA[1]}'")
    op.execute(f"UPDATE historico_fase_lead SET fase = '{_DE_PARA[0]}' WHERE fase = '{_DE_PARA[1]}'")

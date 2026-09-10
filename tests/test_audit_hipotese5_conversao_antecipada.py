"""Auditoria técnica (10/09/2026) — Hipótese 5: "proposta_aceita" converteria
o lead antecipadamente (antes do pagamento)?

Não altera a regra -- só documenta o comportamento atual por evidência de
teste. O caso contrário ("ganho" converte de verdade) já tem cobertura em
tests/test_crm_conversao.py::test_avancar_fase_lead_para_ganho_marca_resultado_ganho
-- não duplicado aqui.
"""

import asyncio

from app.crm import avancar_fase_lead
from app.models import Lead, StatusLead
from tests.conftest import FakeSession


def _lead(**kwargs: object) -> Lead:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "nome": "Cliente Teste",
        "email": "cliente@teste.local",
        "telefone": "11999999999",
        "marca": "ACME",
        "fase": "proposta_enviada",
        "status": StatusLead.PROPOSTA_ENVIADA,
        "resultado": None,
        "motivo_perda": None,
        "motivo_perda_detalhe": None,
    }
    base.update(kwargs)
    return Lead(**base)


def test_avancar_para_proposta_aceita_nao_converte_nem_marca_resultado() -> None:
    """CONFIRMADO (código + teste): MAPA_FASE_STATUS (app/crm.py) não tem
    entrada para "proposta_aceita" -- avancar_fase_lead só sincroniza
    lead.status quando a fase de destino está nesse mapa. Mover para
    "proposta_aceita" muda a fase, mas NÃO muda o status (continua o que
    já era) e NÃO marca resultado="ganho". A conversão de verdade só
    acontece na fase "ganho", que hoje só é atingida ao registrar o
    protocolo no INPI (app/api/leads.py, endpoint de protocolo da
    proposta) -- bem depois de proposta aceita e pagamento confirmado.
    Existe uma via alternativa: um operador pode arrastar manualmente o
    card do Kanban direto para a coluna "Ganho" (mover_lead_kanban usa
    forcar=True), o que forçaria a conversão sem passar pelo protocolo --
    isso é uma flexibilidade manual do Kanban, não um efeito automático de
    "proposta_aceita", e está fora do escopo desta hipótese."""
    import app.crm as crm_modulo

    original = crm_modulo.aplicar_regras_automacao

    async def _fake(*_args: object, **_kwargs: object) -> list[str]:
        return []

    crm_modulo.aplicar_regras_automacao = _fake
    try:
        lead = _lead()
        session = FakeSession([])
        mudou = asyncio.run(avancar_fase_lead(session, lead, "proposta_aceita", "Operador"))
    finally:
        crm_modulo.aplicar_regras_automacao = original

    assert mudou is True
    assert lead.fase == "proposta_aceita"
    assert lead.status == StatusLead.PROPOSTA_ENVIADA, "status não deveria mudar ao entrar em proposta_aceita"
    assert lead.resultado is None, "resultado não deveria virar 'ganho' antes do pagamento/protocolo"

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
        "fase": "relatorio_enviado",
        "status": StatusLead.QUALIFICADO,
        "resultado": None,
        "motivo_perda": None,
        "motivo_perda_detalhe": None,
    }
    base.update(kwargs)
    return Lead(**base)


async def _regras_automacao_fake(*_args: object, **_kwargs: object) -> list[str]:
    return []


def _sem_regras_automacao():
    """Isola avancar_fase_lead de aplicar_regras_automacao (testada à parte)."""
    import app.crm as crm_modulo

    original = crm_modulo.aplicar_regras_automacao
    crm_modulo.aplicar_regras_automacao = _regras_automacao_fake
    return original


def _restaurar_regras_automacao(original) -> None:
    import app.crm as crm_modulo

    crm_modulo.aplicar_regras_automacao = original


# --- Achado L9 do plano Leads/CRM (03/09/2026): resultado não acompanhava a conversão automática ---


def test_avancar_fase_lead_para_proposta_aceita_marca_resultado_ganho() -> None:
    original = _sem_regras_automacao()
    try:
        lead = _lead()
        session = FakeSession([])
        mudou = asyncio.run(avancar_fase_lead(session, lead, "proposta_aceita", "Cliente via link"))
    finally:
        _restaurar_regras_automacao(original)

    assert mudou is True
    assert lead.status == StatusLead.CONVERTIDO
    assert lead.resultado == "ganho"
    assert lead.motivo_perda is None
    assert lead.motivo_perda_detalhe is None


def test_avancar_fase_lead_limpa_motivo_perda_anterior_ao_converter() -> None:
    original = _sem_regras_automacao()
    try:
        lead = _lead(motivo_perda="sem_resposta", motivo_perda_detalhe="Não retornou os contatos.")
        session = FakeSession([])
        asyncio.run(avancar_fase_lead(session, lead, "proposta_aceita", "Cliente via link"))
    finally:
        _restaurar_regras_automacao(original)

    assert lead.resultado == "ganho"
    assert lead.motivo_perda is None
    assert lead.motivo_perda_detalhe is None


def test_avancar_fase_lead_para_fase_sem_status_convertido_nao_altera_resultado() -> None:
    original = _sem_regras_automacao()
    try:
        lead = _lead(fase="contato_inicial", status=StatusLead.EM_CONTATO)
        session = FakeSession([])
        asyncio.run(avancar_fase_lead(session, lead, "relatorio_enviado", "Operador"))
    finally:
        _restaurar_regras_automacao(original)

    assert lead.status == StatusLead.QUALIFICADO
    assert lead.resultado is None


def test_avancar_fase_lead_idempotente_quando_ja_convertido() -> None:
    original = _sem_regras_automacao()
    try:
        lead = _lead(fase="pagamento_realizado", status=StatusLead.CONVERTIDO, resultado="ganho")
        session = FakeSession([])
        mudou = asyncio.run(avancar_fase_lead(session, lead, "protocolo_inpi", "sistema"))
    finally:
        _restaurar_regras_automacao(original)

    assert mudou is True
    assert lead.status == StatusLead.CONVERTIDO
    assert lead.resultado == "ganho"

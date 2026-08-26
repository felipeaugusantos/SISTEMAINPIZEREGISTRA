from datetime import UTC, datetime, timedelta

from app.api.leads import _atualizar_sla_proposta, _prazo_sla_24h
from app.models import PropostaComercial


def _proposta(**kwargs: object) -> PropostaComercial:
    proposta = PropostaComercial(
        organizacao_id=1,
        lead_id=1,
        numero="PROP-TEST",
        escopo="Registro de marca no INPI",
        **kwargs,
    )
    return proposta


def test_prazo_sla_e_24_horas() -> None:
    inicio = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    assert _prazo_sla_24h(inicio) == inicio + timedelta(hours=24)


def test_sla_aguarda_pagamento_apos_aceite() -> None:
    proposta = _proposta(status="aceita")
    assert _atualizar_sla_proposta(proposta) == "aguardando_pagamento"


def test_sla_em_prazo_apos_pagamento() -> None:
    inicio = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    proposta = _proposta(
        status="aceita",
        pagamento_status="confirmado",
        sla_inicio_em=inicio,
        sla_prazo_em=_prazo_sla_24h(inicio),
    )
    assert _atualizar_sla_proposta(proposta, inicio + timedelta(hours=1)) == "em_prazo"


def test_sla_vencido_sem_protocolo() -> None:
    inicio = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    proposta = _proposta(
        status="aceita",
        pagamento_status="confirmado",
        sla_inicio_em=inicio,
        sla_prazo_em=_prazo_sla_24h(inicio),
    )
    assert _atualizar_sla_proposta(proposta, inicio + timedelta(hours=25)) == "vencido"


def test_protocolo_concluido_tem_precedencia() -> None:
    proposta = _proposta(status="aceita", protocolo_em=datetime.now(UTC))
    assert _atualizar_sla_proposta(proposta) == "protocolado"


def test_proposta_pode_preservar_a_pesquisa_de_origem() -> None:
    proposta = _proposta(pesquisa_id="12345678-1234-1234-1234-123456789abc")
    assert proposta.pesquisa_id == "12345678-1234-1234-1234-123456789abc"

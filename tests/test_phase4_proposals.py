import asyncio
from datetime import UTC, date, datetime, timedelta

from starlette.requests import Request

from app.api.leads import _atualizar_sla_proposta, _prazo_sla_24h, aceitar_proposta_publica
from app.models import AssinaturaPropostaComercial, Organizacao, PropostaComercial
from tests.conftest import FakeResult, FakeSession


def _proposta(**kwargs: object) -> PropostaComercial:
    proposta = PropostaComercial(
        organizacao_id=1,
        lead_id=1,
        numero="PROP-TEST",
        escopo="Registro de marca no INPI",
        **kwargs,
    )
    return proposta


def _request_post(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


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


# --- Fase 1 do plano proposta-financeiro (03/09/2026): blindar o aceite ---


def _proposta_com_token(**kwargs: object) -> PropostaComercial:
    base: dict = {
        "id": 1,
        "status": "enviada",
        "public_token_hash": "hash-qualquer",
        "public_token_expira_em": datetime.now(UTC) + timedelta(days=1),
        "honorarios": 1500,
        "taxa_gru": 355,
        "condicoes_pagamento": "À vista",
    }
    base.update(kwargs)
    return _proposta(**base)


def test_aceitar_proposta_publica_rejeita_quando_validade_expirou() -> None:
    proposta = _proposta_com_token(validade_em=date(2020, 1, 1))
    session = FakeSession([FakeResult(scalar=proposta)])
    resposta = asyncio.run(aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session))
    assert resposta.status_code == 409
    assert proposta.status == "enviada"
    assert proposta.public_aceito_em is None
    assert session.adicionados == []


def test_aceitar_proposta_publica_ja_aceita_continua_idempotente_mesmo_apos_expirar() -> None:
    ja_aceita_em = datetime(2020, 1, 2, tzinfo=UTC)
    proposta = _proposta_com_token(
        validade_em=date(2020, 1, 1),
        status="aceita",
        public_aceito_em=ja_aceita_em,
        aceito_em=ja_aceita_em,
    )
    session = FakeSession([FakeResult(scalar=proposta)])
    resposta = asyncio.run(aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session))
    assert resposta.status_code == 200
    assert proposta.public_aceito_em == ja_aceita_em


def test_aceitar_proposta_publica_dentro_da_validade_prossegue_com_o_aceite() -> None:
    import app.api.leads as leads_modulo

    async def _avancar_fake(*_args: object, **_kwargs: object) -> bool:
        return True

    original = leads_modulo.avancar_fase_lead
    leads_modulo.avancar_fase_lead = _avancar_fake
    try:
        proposta = _proposta_com_token(validade_em=date(2099, 12, 31))
        session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])
        resposta = asyncio.run(
            leads_modulo.aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session)
        )
    finally:
        leads_modulo.avancar_fase_lead = original

    assert resposta.status_code == 200
    assert proposta.status == "aceita"
    assert proposta.public_aceito_em is not None
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert len(assinaturas) == 1


def test_visualizar_proposta_publica_exibe_valores_condicoes_e_validade() -> None:
    import app.api.leads as leads_modulo

    proposta = _proposta_com_token(marca="ACME", classes="35", validade_em=date(2099, 12, 31))
    session = FakeSession([FakeResult(scalar=proposta)])

    async def _get_fake(*_args: object, **_kwargs: object) -> Organizacao:
        return Organizacao(id=1, nome="Zé Registra", slug="ze-registra")

    session.get = _get_fake
    resultado = asyncio.run(leads_modulo.visualizar_proposta_publica("token-qualquer", session))
    corpo = resultado.body.decode("utf-8")
    assert "1.500,00" in corpo
    assert "355,00" in corpo
    assert "1.855,00" in corpo
    assert "À vista" in corpo
    assert "31/12/2099" in corpo


def test_aceitar_proposta_publica_sem_validade_definida_nao_e_bloqueada() -> None:
    import app.api.leads as leads_modulo

    async def _avancar_fake(*_args: object, **_kwargs: object) -> bool:
        return True

    original = leads_modulo.avancar_fase_lead
    leads_modulo.avancar_fase_lead = _avancar_fake
    try:
        proposta = _proposta_com_token(validade_em=None)
        session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])
        resposta = asyncio.run(
            leads_modulo.aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session)
        )
    finally:
        leads_modulo.avancar_fase_lead = original

    assert resposta.status_code == 200
    assert proposta.status == "aceita"

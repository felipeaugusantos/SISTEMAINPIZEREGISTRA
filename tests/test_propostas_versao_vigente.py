"""Achados 18.1 e 18.2 da auditoria fina de Propostas comerciais (29/09/2026).

18.1: criar uma nova versão não invalidava a anterior -- ela continuava
"enviada", com o link público ativo, e o cliente podia aceitar a versão
antiga (preço antigo) ou as duas, gerando duas contratações.

18.2: cada canal de aceite checava o status do seu jeito -- o webhook do
Clicksign aceitava proposta cancelada/recusada, a confirmação do código do
link público não rechecava o status e o envio da proposta forçava "enviada"
(rebaixando uma proposta aceita ou revivendo uma cancelada).

Isolados e determinísticos: FakeSession (tests/conftest.py), sem banco real.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException

import app.api.leads_propostas as leads_propostas
from app.api.leads_propostas import (
    aceite_ja_registrado,
    confirmar_codigo_proposta,
    criar_nova_versao_proposta,
    motivo_bloqueio_aceite,
    visualizar_proposta_publica,
)
from app.api.portal_cliente import AssinarComCodigoInput, assinar_proposta_portal, webhook_clicksign
from app.auth import hash_token
from app.models import ContratacaoServico, EventoDominio, Lead, PropostaComercial
from tests.conftest import FakeResult, FakeSession, usuario_teste
from tests.test_phase4_proposals import _request_post
from tests.test_portal_cliente import (
    _CODIGO_CONFIRMACAO_TESTE,
    _assinatura_webhook,
    _cliente,
    _configurar_segredo_webhook,
    _request,
    _webhook_request,
)


def _proposta(**kwargs: object) -> PropostaComercial:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "lead_id": 9,
        "numero": "PROP-2026-000010",
        "versao": 1,
        "escopo": "Registro de marca no INPI",
        "status": "enviada",
        "honorarios": 1500,
        "taxa_gru": 415,
        "public_token_hash": "hash-qualquer",
        "public_token_expira_em": datetime.now(UTC) + timedelta(days=3),
        "dados": {"conta_contabil_honorarios_id": 11, "conta_contabil_taxa_gru_id": 12},
    }
    base.update(kwargs)
    return PropostaComercial(**base)


# --- regra única ------------------------------------------------------------


@pytest.mark.parametrize("status", ["rascunho", "aceita", "recusada", "expirada", "cancelada"])
def test_motivo_bloqueio_recusa_status_que_nao_aceita(status: str) -> None:
    assert motivo_bloqueio_aceite(_proposta(status=status)) is not None


def test_motivo_bloqueio_recusa_proposta_vencida() -> None:
    assert "expirou" in motivo_bloqueio_aceite(_proposta(validade_em=date(2020, 1, 1)))


@pytest.mark.parametrize("status", ["enviada", "visualizada"])
def test_motivo_bloqueio_libera_proposta_vigente(status: str) -> None:
    assert motivo_bloqueio_aceite(_proposta(status=status, validade_em=date(2099, 12, 31))) is None


# --- 18.1: nova versão invalida a anterior -----------------------------------


def test_nova_versao_cancela_a_anterior_e_revoga_o_link() -> None:
    anterior = _proposta(
        status="enviada",
        codigo_confirmacao_hash="hash-do-codigo",
        dados={
            "conta_contabil_honorarios_id": 11,
            "conta_contabil_taxa_gru_id": 12,
            "clicksign": {"envelope_id": "env-antigo"},
        },
    )
    session = FakeSession([FakeResult(scalar=anterior), FakeResult(scalar=1)])

    resultado = asyncio.run(
        criar_nova_versao_proposta(1, _request_post("/propostas/1/nova-versao"), session, usuario_teste())
    )

    assert resultado["versao"] == 2
    assert anterior.status == "cancelada"
    assert anterior.dados["cancelamento"]["motivo"] == "Substituída pela versão 2"
    assert anterior.public_token_hash is None
    assert anterior.public_token_expira_em is None
    assert anterior.codigo_confirmacao_hash is None
    nova = next(obj for obj in session.adicionados if isinstance(obj, PropostaComercial))
    # O envelope do Clicksign pertence à versão antiga: se fosse copiado, a
    # assinatura do documento antigo seria aplicada à versão nova.
    assert "clicksign" not in nova.dados
    assert "cancelamento" not in nova.dados


def test_nova_versao_de_proposta_aceita_e_recusada() -> None:
    anterior = _proposta(status="aceita")
    session = FakeSession([FakeResult(scalar=anterior)])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(criar_nova_versao_proposta(1, _request_post("/propostas/1/nova-versao"), session, usuario_teste()))

    assert erro.value.status_code == 422
    assert anterior.status == "aceita"
    assert not [obj for obj in session.adicionados if isinstance(obj, PropostaComercial)]


def test_nova_versao_exige_partir_da_versao_mais_recente() -> None:
    anterior = _proposta(status="cancelada", versao=1)
    session = FakeSession([FakeResult(scalar=anterior), FakeResult(scalar=3)])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(criar_nova_versao_proposta(1, _request_post("/propostas/1/nova-versao"), session, usuario_teste()))

    assert erro.value.status_code == 422
    assert "versão 3" in erro.value.detail


def test_nova_versao_de_proposta_recusada_mantem_o_status_e_revoga_o_link() -> None:
    anterior = _proposta(status="recusada")
    session = FakeSession([FakeResult(scalar=anterior), FakeResult(scalar=1)])

    asyncio.run(criar_nova_versao_proposta(1, _request_post("/propostas/1/nova-versao"), session, usuario_teste()))

    assert anterior.status == "recusada"
    assert anterior.public_token_hash is None


# --- 18.2: canais de aceite ---------------------------------------------------


def test_confirmar_codigo_de_proposta_cancelada_depois_do_codigo_e_recusado() -> None:
    proposta = _proposta(
        status="cancelada",
        codigo_confirmacao_hash=hash_token("123456"),
        codigo_confirmacao_expira_em=datetime.now(UTC) + timedelta(minutes=10),
        codigo_confirmacao_tentativas=0,
    )
    session = FakeSession([FakeResult(scalar=proposta)])

    resposta = asyncio.run(
        confirmar_codigo_proposta("token-qualquer", _request_post("/propostas/x/confirmar"), session, "123456")
    )

    assert resposta.status_code == 409
    assert proposta.status == "cancelada"
    assert proposta.public_aceito_em is None
    assert not [obj for obj in session.adicionados if isinstance(obj, ContratacaoServico)]


def test_aceitar_link_de_proposta_cancelada_e_recusado() -> None:
    proposta = _proposta(status="cancelada")
    session = FakeSession([FakeResult(scalar=proposta)])

    resposta = asyncio.run(
        leads_propostas.aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session)
    )

    assert resposta.status_code == 409
    assert proposta.codigo_confirmacao_hash is None


def test_pagina_publica_de_proposta_cancelada_nao_mostra_o_botao_de_aceite() -> None:
    proposta = _proposta(status="cancelada")
    session = FakeSession([FakeResult(scalar=proposta)], objetos_get=[_organizacao()])

    resposta = asyncio.run(visualizar_proposta_publica("token-qualquer", session))

    corpo = resposta.body.decode("utf-8")
    assert "Aceitar proposta" not in corpo
    assert "não está mais disponível" in corpo


def test_webhook_clicksign_nao_aceita_proposta_cancelada(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar_segredo_webhook(monkeypatch)
    proposta = _proposta(id=7, status="cancelada", dados={"clicksign": {"envelope_id": "env-123"}})
    session = FakeSession([FakeResult(scalar=proposta)])
    corpo = {"envelope_id": "env-123", "event_id": "evt-1", "status": "document_closed"}

    resultado = asyncio.run(webhook_clicksign(_webhook_request(corpo), session, _assinatura_webhook(corpo)))

    assert resultado == {"ok": True, "ignorado": True, "proposta_id": 7}
    assert proposta.status == "cancelada"
    assert proposta.aceito_em is None
    assert not [obj for obj in session.adicionados if isinstance(obj, ContratacaoServico)]


def test_assinar_no_portal_proposta_cancelada_e_recusado() -> None:
    proposta = _proposta(status="cancelada")
    session = FakeSession([FakeResult(scalar=proposta)])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)

    with pytest.raises(HTTPException) as erro:
        asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))

    assert erro.value.status_code == 409
    assert proposta.status == "cancelada"


def _organizacao():
    from app.models import Organizacao

    return Organizacao(id=1, nome="Escritório Teste", slug="escritorio-teste")


def _lead() -> Lead:
    return Lead(id=9, organizacao_id=1, nome="Cliente", email="cliente@example.com", telefone="", marca="ACME")


def _isolar_envio(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    enviados: list[str] = []

    async def _enviar_fake(destinatario, *_args, **_kwargs) -> None:
        enviados.append(destinatario)

    monkeypatch.setattr(leads_propostas, "enviar_proposta_email", _enviar_fake)
    monkeypatch.setattr(leads_propostas, "gerar_pdf_proposta", lambda _dados: b"%PDF-teste")
    monkeypatch.setattr(leads_propostas, "configuracao_clicksign", lambda _org=None: {"enabled": False})
    return enviados


@pytest.mark.parametrize("status", ["aceita", "cancelada", "recusada", "expirada"])
def test_enviar_proposta_nao_rebaixa_nem_revive_o_status(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    enviados = _isolar_envio(monkeypatch)
    proposta = _proposta(status=status)
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=_lead())])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(leads_propostas.enviar_link_proposta(1, _request_post("/propostas/1/enviar"), session, usuario_teste()))

    assert erro.value.status_code == 422
    assert proposta.status == status
    assert enviados == []


def test_reenviar_proposta_visualizada_mantem_o_status(monkeypatch: pytest.MonkeyPatch) -> None:
    enviados = _isolar_envio(monkeypatch)
    proposta = _proposta(status="visualizada")
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=_lead())], objetos_get=[_organizacao()])

    asyncio.run(leads_propostas.enviar_link_proposta(1, _request_post("/propostas/1/enviar"), session, usuario_teste()))

    assert proposta.status == "visualizada"
    assert enviados == ["cliente@example.com"]


def test_enviar_rascunho_passa_para_enviada(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolar_envio(monkeypatch)
    proposta = _proposta(status="rascunho")
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=_lead())], objetos_get=[_organizacao()])

    asyncio.run(leads_propostas.enviar_link_proposta(1, _request_post("/propostas/1/enviar"), session, usuario_teste()))

    assert proposta.status == "enviada"


# --- Revisão do Codex no PR #147 ---------------------------------------------


def _aceita_e_depois_cancelada(**kwargs: object) -> PropostaComercial:
    aceita_em = datetime(2026, 9, 1, tzinfo=UTC)
    base: dict = {"status": "cancelada", "public_aceito_em": aceita_em, "aceito_em": aceita_em}
    base.update(kwargs)
    return _proposta(**base)


def test_aceite_ja_registrado_perde_para_status_encerrado() -> None:
    assert aceite_ja_registrado(_proposta(status="aceita")) is True
    assert aceite_ja_registrado(_aceita_e_depois_cancelada()) is False
    assert aceite_ja_registrado(_proposta(status="enviada")) is False


def test_link_de_proposta_aceita_e_depois_cancelada_nao_se_apresenta_como_aceita() -> None:
    proposta = _aceita_e_depois_cancelada()

    resposta = asyncio.run(
        leads_propostas.aceitar_proposta_publica(
            "token-qualquer", _request_post("/propostas/x/aceitar"), FakeSession([FakeResult(scalar=proposta)])
        )
    )
    confirmacao = asyncio.run(
        confirmar_codigo_proposta(
            "token-qualquer", _request_post("/propostas/x/confirmar"), FakeSession([FakeResult(scalar=proposta)]), "1"
        )
    )

    assert resposta.status_code == 409
    assert confirmacao.status_code == 409


def test_portal_nao_aceita_de_novo_proposta_aceita_e_depois_cancelada() -> None:
    proposta = _aceita_e_depois_cancelada()
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)

    with pytest.raises(HTTPException) as erro:
        asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), FakeSession([FakeResult(scalar=proposta)])))

    assert erro.value.status_code == 409


def test_webhook_clicksign_nao_reativa_proposta_aceita_e_depois_cancelada(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar_segredo_webhook(monkeypatch)
    proposta = _aceita_e_depois_cancelada(id=7, dados={"clicksign": {"envelope_id": "env-123"}})
    session = FakeSession([FakeResult(scalar=proposta)])
    corpo = {"envelope_id": "env-123", "event_id": "evt-9", "status": "document_closed"}

    resultado = asyncio.run(webhook_clicksign(_webhook_request(corpo), session, _assinatura_webhook(corpo)))

    assert resultado["ignorado"] is True
    assert proposta.status == "cancelada"


def test_webhook_clicksign_repetido_registra_a_assinatura_ignorada_uma_vez(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar_segredo_webhook(monkeypatch)
    proposta = _proposta(id=7, status="cancelada", dados={"clicksign": {"envelope_id": "env-123"}})
    corpo = {"envelope_id": "env-123", "event_id": "evt-1", "status": "document_closed"}
    eventos = 0
    for _ in range(3):
        session = FakeSession([FakeResult(scalar=proposta)])
        asyncio.run(webhook_clicksign(_webhook_request(corpo), session, _assinatura_webhook(corpo)))
        eventos += len([obj for obj in session.adicionados if isinstance(obj, EventoDominio)])

    assert eventos == 1
    assert proposta.dados["clicksign"]["assinatura_indisponivel_em"]


def test_nova_versao_e_aceites_travam_a_linha_da_proposta() -> None:
    """Nova versão e aceite concorrentes viam a proposta "enviada" ao mesmo
    tempo -- o aceite gerava a cobrança e a nova versão ainda podia ser
    aceita depois. Todos passam a travar a mesma linha (FOR UPDATE)."""
    from sqlalchemy.dialects import postgresql

    def _sql(stmt) -> str:
        return str(stmt.compile(dialect=postgresql.dialect()))

    sessao_versao = FakeSession([FakeResult(scalar=_proposta()), FakeResult(scalar=1)])
    asyncio.run(criar_nova_versao_proposta(1, _request_post("/propostas/1/nova-versao"), sessao_versao, usuario_teste()))
    assert "FOR UPDATE" in _sql(sessao_versao.executados[0])

    sessao_link = FakeSession([FakeResult(scalar=_proposta(status="cancelada"))])
    asyncio.run(
        confirmar_codigo_proposta("token-qualquer", _request_post("/propostas/x/confirmar"), sessao_link, "1")
    )
    assert "FOR UPDATE" in _sql(sessao_link.executados[0])


def test_gerar_link_de_proposta_cancelada_e_recusado() -> None:
    proposta = _proposta(status="cancelada", public_token_hash=None)
    session = FakeSession([FakeResult(scalar=proposta)])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(leads_propostas.criar_link_proposta(1, _request_post("/propostas/1/link"), session, usuario_teste()))

    assert erro.value.status_code == 422
    assert proposta.public_token_hash is None

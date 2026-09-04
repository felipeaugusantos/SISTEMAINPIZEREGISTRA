import asyncio
import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.pagamentos import CobrancaInput, criar_cobranca_pagamento, receber_webhook_pagamento
from app.models import LancamentoFinanceiro, ParcelaFinanceira, WebhookFinanceiro
from tests.conftest import FakeResult, FakeSession, usuario_teste

SEGREDO = "segredo-teste"


@pytest.fixture(autouse=True)
def _configurar_segredo_do_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """obter_adaptador() lê o segredo via app.settings.get_settings() em
    tempo de chamada (import local) -- sem isso, o segredo real do
    ambiente de teste (vazio por padrão) faria toda assinatura "correta"
    destes testes falhar a verificação."""
    from app import settings as settings_module

    copia = settings_module.get_settings().model_copy(update={"gateway_webhook_secret": SEGREDO})
    monkeypatch.setattr(settings_module, "get_settings", lambda: copia)


def _request_com_corpo(corpo: bytes) -> Request:
    async def _receive() -> dict:
        return {"type": "http.request", "body": corpo, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/webhooks/pagamentos/sandbox",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
        },
        receive=_receive,
    )


def _assinar(corpo: bytes) -> str:
    return hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()


def _parcela(**kwargs: object) -> ParcelaFinanceira:
    lancamento = LancamentoFinanceiro(
        id=1, organizacao_id=1, tipo="receber", lead_id=None, proposta_id=None, status="aberto"
    )
    base: dict = {
        "id": 5,
        "organizacao_id": 1,
        "lancamento_id": 1,
        "numero": 1,
        "vencimento": date(2026, 1, 1),
        "valor": Decimal("500.00"),
        "valor_pago": Decimal("0"),
        "status": "aberta",
    }
    base.update(kwargs)
    parcela = ParcelaFinanceira(**base)
    parcela.lancamento = lancamento
    lancamento.parcelas = [parcela]
    return parcela


# --- Achado FASE7-1/2 da auditoria (04/09/2026): webhook de pagamento
# unificado -- um único caminho, atrás da interface de adaptador. ---


def test_webhook_adaptador_desconhecido_retorna_404() -> None:
    corpo = json.dumps({}).encode()
    session = FakeSession()
    try:
        asyncio.run(receber_webhook_pagamento("inexistente", _request_com_corpo(corpo), session, None))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 404


def test_webhook_sem_assinatura_retorna_401() -> None:
    corpo = json.dumps({"referencia": "r1", "organizacao_id": 1, "parcela_id": 5, "status": "paid", "valor": "500"}).encode()
    session = FakeSession()
    try:
        asyncio.run(receber_webhook_pagamento("sandbox", _request_com_corpo(corpo), session, None))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 401


def test_webhook_pago_baixa_parcela_e_e_idempotente() -> None:
    parcela = _parcela()
    corpo = json.dumps(
        {"referencia": "ref-pagamento-1", "organizacao_id": 1, "parcela_id": 5, "status": "paid", "valor": "500.00"}
    ).encode()
    session = FakeSession(
        [
            FakeResult(scalar=None),  # dedup: nenhum WebhookFinanceiro com essa referência ainda
            FakeResult(scalar=parcela),  # busca a parcela
        ]
    )

    resultado = asyncio.run(
        receber_webhook_pagamento("sandbox", _request_com_corpo(corpo), session, _assinar(corpo))
    )

    assert resultado == {"idempotente": False, "status": "pago"}
    assert parcela.status == "paga"
    assert parcela.valor_pago == Decimal("500.00")
    webhooks_criados = [obj for obj in session.adicionados if isinstance(obj, WebhookFinanceiro)]
    assert len(webhooks_criados) == 1
    assert session.commits == 1


def test_webhook_repetido_e_idempotente() -> None:
    evento_existente = WebhookFinanceiro(
        id=1, organizacao_id=1, referencia="ref-pagamento-1", evento="pagamento.pago", payload={}, status="pago"
    )
    corpo = json.dumps(
        {"referencia": "ref-pagamento-1", "organizacao_id": 1, "parcela_id": 5, "status": "paid", "valor": "500.00"}
    ).encode()
    session = FakeSession([FakeResult(scalar=evento_existente)])

    resultado = asyncio.run(
        receber_webhook_pagamento("sandbox", _request_com_corpo(corpo), session, _assinar(corpo))
    )

    assert resultado == {"idempotente": True, "status": "pago"}
    assert session.adicionados == []


def test_webhook_valor_divergente_retorna_422() -> None:
    parcela = _parcela(valor=Decimal("500.00"))
    corpo = json.dumps(
        {"referencia": "ref-pagamento-1", "organizacao_id": 1, "parcela_id": 5, "status": "paid", "valor": "999.00"}
    ).encode()
    session = FakeSession([FakeResult(scalar=None), FakeResult(scalar=parcela)])

    try:
        asyncio.run(receber_webhook_pagamento("sandbox", _request_com_corpo(corpo), session, _assinar(corpo)))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


def test_webhook_estornado_reverte_parcela_paga() -> None:
    parcela = _parcela(status="paga", valor_pago=Decimal("500.00"), pago_em=date(2026, 1, 5))
    corpo = json.dumps(
        {"referencia": "ref-estorno-1", "organizacao_id": 1, "parcela_id": 5, "status": "refunded", "valor": "500.00"}
    ).encode()
    session = FakeSession([FakeResult(scalar=None), FakeResult(scalar=parcela), FakeResult(scalar=None)])

    resultado = asyncio.run(
        receber_webhook_pagamento("sandbox", _request_com_corpo(corpo), session, _assinar(corpo))
    )

    assert resultado == {"idempotente": False, "status": "estornado"}
    assert parcela.status == "aberta"
    assert parcela.valor_pago == Decimal(0)
    assert parcela.pago_em is None


def test_webhook_estornado_de_parcela_ja_aberta_nao_faz_nada() -> None:
    parcela = _parcela(status="aberta")
    corpo = json.dumps(
        {"referencia": "ref-estorno-2", "organizacao_id": 1, "parcela_id": 5, "status": "refunded", "valor": "500.00"}
    ).encode()
    session = FakeSession([FakeResult(scalar=None), FakeResult(scalar=parcela)])

    resultado = asyncio.run(
        receber_webhook_pagamento("sandbox", _request_com_corpo(corpo), session, _assinar(corpo))
    )

    assert resultado == {"idempotente": False, "status": "estornado"}
    assert parcela.status == "aberta"


# --- Criação de cobrança (Pix/boleto) ---


def test_criar_cobranca_parcela_inexistente_retorna_404() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    try:
        asyncio.run(
            criar_cobranca_pagamento(
                "sandbox", CobrancaInput(parcela_id=999, tipo="pix"), session, usuario_teste()
            )
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 404


def test_criar_cobranca_parcela_ja_paga_retorna_409() -> None:
    parcela = _parcela(status="paga")
    session = FakeSession([FakeResult(scalar=parcela)])
    try:
        asyncio.run(
            criar_cobranca_pagamento("sandbox", CobrancaInput(parcela_id=5, tipo="pix"), session, usuario_teste())
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409


def test_criar_cobranca_pix_com_sucesso() -> None:
    parcela = _parcela()
    session = FakeSession([FakeResult(scalar=parcela)])

    resultado = asyncio.run(
        criar_cobranca_pagamento("sandbox", CobrancaInput(parcela_id=5, tipo="pix"), session, usuario_teste())
    )

    assert resultado["tipo"] == "pix"
    assert resultado["qr_code"] is not None

import hashlib
import hmac
from decimal import Decimal

import pytest

from app.pagamentos import (
    AdaptadorIndisponivelError,
    AdaptadorSandbox,
    obter_adaptador,
)

# --- Achado FASE7-1/2 da auditoria (04/09/2026): interface de adaptador de
# pagamento (Pix/boleto), sem acoplar a um fornecedor -- AdaptadorSandbox é
# a implementação de referência, nunca fala com um PSP real. ---


async def test_criar_cobranca_pix_devolve_qr_code() -> None:
    adaptador = AdaptadorSandbox("segredo")
    cobranca = await adaptador.criar_cobranca(
        tipo="pix", valor=Decimal("100"), referencia="ref-1", descricao="teste", vencimento=None
    )
    assert cobranca.tipo == "pix"
    assert cobranca.qr_code is not None
    assert cobranca.linha_digitavel is None


async def test_criar_cobranca_boleto_devolve_linha_digitavel() -> None:
    adaptador = AdaptadorSandbox("segredo")
    cobranca = await adaptador.criar_cobranca(
        tipo="boleto", valor=Decimal("100"), referencia="ref-1", descricao="teste", vencimento=None
    )
    assert cobranca.tipo == "boleto"
    assert cobranca.linha_digitavel is not None
    assert cobranca.qr_code is None


async def test_criar_cobranca_tipo_invalido_levanta_erro() -> None:
    adaptador = AdaptadorSandbox("segredo")
    with pytest.raises(ValueError):
        await adaptador.criar_cobranca(
            tipo="cartao", valor=Decimal("100"), referencia="ref-1", descricao="x", vencimento=None
        )


def test_verificar_assinatura_correta() -> None:
    adaptador = AdaptadorSandbox("segredo")
    corpo = b'{"a": 1}'
    assinatura = hmac.new(b"segredo", corpo, hashlib.sha256).hexdigest()
    assert adaptador.verificar_assinatura(corpo, assinatura) is True


def test_verificar_assinatura_incorreta() -> None:
    adaptador = AdaptadorSandbox("segredo")
    assert adaptador.verificar_assinatura(b'{"a": 1}', "assinatura-errada") is False


def test_verificar_assinatura_sem_segredo_configurado() -> None:
    adaptador = AdaptadorSandbox(None)
    assert adaptador.verificar_assinatura(b'{"a": 1}', "qualquer") is False


def test_verificar_assinatura_sem_header() -> None:
    adaptador = AdaptadorSandbox("segredo")
    assert adaptador.verificar_assinatura(b'{"a": 1}', None) is False


def test_interpretar_webhook_normaliza_status_pago() -> None:
    adaptador = AdaptadorSandbox("segredo")
    evento = adaptador.interpretar_webhook(
        {"organizacao_id": 1, "referencia": "ref-1", "parcela_id": 5, "status": "paid", "valor": "150.00"}
    )
    assert evento.status == "pago"
    assert evento.valor == Decimal("150.00")
    assert evento.parcela_id == 5


def test_interpretar_webhook_status_desconhecido_levanta_erro() -> None:
    adaptador = AdaptadorSandbox("segredo")
    with pytest.raises(ValueError):
        adaptador.interpretar_webhook(
            {"organizacao_id": 1, "referencia": "ref-1", "parcela_id": 5, "status": "algo_estranho", "valor": "1"}
        )


def test_interpretar_webhook_payload_incompleto_levanta_erro() -> None:
    adaptador = AdaptadorSandbox("segredo")
    with pytest.raises(ValueError):
        adaptador.interpretar_webhook({"status": "paid"})


def test_obter_adaptador_sandbox() -> None:
    adaptador = obter_adaptador("sandbox")
    assert adaptador.nome == "sandbox"


def test_obter_adaptador_desconhecido_levanta_erro() -> None:
    with pytest.raises(AdaptadorIndisponivelError):
        obter_adaptador("mercadopago")

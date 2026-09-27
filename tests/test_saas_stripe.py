import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.saas import CheckoutSaasInput, PlanoPrecosInput, _validar_assinatura_stripe
from app.main import app
from app.settings import Settings


def test_checkout_aceita_apenas_ciclos_mensal_e_anual() -> None:
    assert CheckoutSaasInput(intervalo="mensal").intervalo == "mensal"
    assert CheckoutSaasInput(intervalo="anual").intervalo == "anual"
    with pytest.raises(ValidationError):
        CheckoutSaasInput(intervalo="semanal")


def test_precos_stripe_sao_opcionais_e_limitados() -> None:
    assert PlanoPrecosInput().stripe_price_mensal_id is None
    with pytest.raises(ValidationError):
        PlanoPrecosInput(stripe_price_anual_id="x" * 151)


def test_stripe_nao_pode_ser_ativado_sem_segredos() -> None:
    with pytest.raises(ValueError, match="STRIPE_SAAS_SECRET_KEY"):
        Settings(stripe_saas_enabled=True)
    cfg = Settings(
        stripe_saas_enabled=True,
        stripe_saas_secret_key="sk_test_example",
        stripe_saas_webhook_secret="whsec_example",
    )
    assert cfg.stripe_saas_enabled


def test_retorno_checkout_e_pagina_publica() -> None:
    resposta = TestClient(app).get("/contratacao/retorno")
    assert resposta.status_code == 200
    assert "verificando o status do pagamento" in resposta.text.lower()


def test_webhook_stripe_confere_assinatura_raw_body_e_timestamp() -> None:
    secret = "whsec_test_secret"
    body = b'{"id":"evt_test","type":"invoice.paid"}'
    timestamp = 1_800_000_000
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    header = f"t={timestamp},v1={digest}"

    assert _validar_assinatura_stripe(body, header, secret, timestamp)
    assert not _validar_assinatura_stripe(body + b" ", header, secret, timestamp)
    assert not _validar_assinatura_stripe(body, header, secret, timestamp + 301)
    assert not _validar_assinatura_stripe(body, "t=nope,v1=bad", secret, timestamp)

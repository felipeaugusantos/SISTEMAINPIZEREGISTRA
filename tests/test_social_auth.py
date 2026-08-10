from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.social_auth import _destino_seguro, _pkce, _validar_claims
from app.main import app


def test_catalogo_social_nao_expoe_credenciais() -> None:
    resposta = TestClient(app).get("/v1/auth/social/providers")

    assert resposta.status_code == 200
    assert resposta.json() == {
        "providers": [
            {"id": "google", "nome": "Google", "enabled": False},
            {"id": "apple", "nome": "Apple", "enabled": False},
        ]
    }


def test_provedor_desativado_nao_inicia_fluxo() -> None:
    resposta = TestClient(app).get(
        "/v1/auth/social/google/start", follow_redirects=False
    )

    assert resposta.status_code == 404


def test_destino_oauth_precisa_ser_local() -> None:
    assert _destino_seguro("/admin/leads") == "/admin/leads"
    assert _destino_seguro("https://malicioso.example") == "/admin"
    assert _destino_seguro("//malicioso.example") == "/admin"


def test_pkce_e_deterministico_e_sem_padding() -> None:
    assert _pkce("verificador") == "Eh2lhg0GRim7FAnnu_O-Mr16osuv3YZvFY23EBiNySY"
    assert "=" not in _pkce("verificador")


def test_claims_exigem_emissor_audiencia_validade_subject_e_nonce() -> None:
    agora = int(datetime.now(UTC).timestamp())
    config = {"issuer": "https://issuer.example", "client_id": "cliente"}
    claims = {
        "iss": config["issuer"],
        "aud": config["client_id"],
        "exp": agora + 300,
        "sub": "usuario-estavel",
        "nonce": "nonce-unico",
    }

    _validar_claims(claims, config, "nonce-unico")

    with pytest.raises(ValueError):
        _validar_claims({**claims, "aud": "outro-cliente"}, config, "nonce-unico")
    with pytest.raises(ValueError):
        _validar_claims(claims, config, "nonce-diferente")
    with pytest.raises(ValueError):
        _validar_claims({**claims, "exp": agora - 120}, config, "nonce-unico")

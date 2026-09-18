import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.social_auth import (
    MFA_COOKIE,
    CodigoMfaSocialInput,
    _destino_seguro,
    _pkce,
    _validar_claims,
    concluir_mfa,
)
from app.auth import hash_token
from app.main import app
from app.models import TentativaOAuth, UsuarioOperacoes
from tests.conftest import FakeResult, FakeSession


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
    resposta = TestClient(app).get("/v1/auth/social/google/start", follow_redirects=False)

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


# --- Achado da varredura ampla do sistema (18/09/2026): mesmo bug de ordem
# RLS/tenant já corrigido em outros pontos de autenticação (ex.:
# portal_cliente.py::logout_cliente) -- consumir um código de recuperação MFA
# mutava Usuario ANTES de aplicar_contexto_tenant, arriscando StaleDataError
# ou a escrita ser silenciosamente descartada pelo RLS. ---


def test_concluir_mfa_aplica_tenant_antes_de_consumir_codigo_recuperacao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.social_auth as modulo_social_auth

    codigo_hash = hash_token("ABC123")
    usuario = UsuarioOperacoes(
        id=7,
        organizacao_id=3,
        nome="Operador Teste",
        usuario="operador",
        email="operador@teste.local",
        perfil="operador",
        senha_hash="hash",
        ativo=True,
        superadmin=False,
        mfa_ativo=True,
        mfa_segredo=None,
        mfa_segredo_versao=modulo_social_auth.versao_chave_atual(),
        codigos_recuperacao=[codigo_hash],
        permissoes=[],
    )
    tentativa = TentativaOAuth(
        id=9,
        provedor="google",
        state_hash="state-hash",
        browser_token_hash="browser-hash",
        nonce="nonce",
        code_verifier="verifier",
        modo="login",
        usuario_id=usuario.id,
        destino="/algum-destino",
        mfa_token_hash=hash_token("desafio-mfa"),
        expira_em=datetime.now(UTC) + timedelta(minutes=5),
    )
    session = FakeSession(
        [
            FakeResult(),  # aplicar_contexto_autenticacao (SET config)
            FakeResult(scalar=tentativa),
            FakeResult(scalar=usuario),
        ]
    )
    ordem: list[str] = []

    async def aplicar_tenant_sem_autoflush(_session, organizacao_id: int, superadmin: bool = False) -> None:
        assert organizacao_id == usuario.organizacao_id
        assert usuario.codigos_recuperacao == [codigo_hash]
        ordem.append("tenant")

    monkeypatch.setattr(modulo_social_auth, "aplicar_contexto_tenant", aplicar_tenant_sem_autoflush)
    monkeypatch.setattr(modulo_social_auth, "validar_totp", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(modulo_social_auth, "revelar_segredo", lambda *_args, **_kwargs: "")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/auth/social/mfa",
            "headers": [(b"cookie", f"{MFA_COOKIE}=desafio-mfa".encode())],
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )
    response = Response()

    resultado = asyncio.run(
        concluir_mfa(CodigoMfaSocialInput(codigo="abc123"), request, response, session)
    )

    assert resultado["status"] == "ok"
    assert ordem == ["tenant"]
    assert usuario.codigos_recuperacao == []

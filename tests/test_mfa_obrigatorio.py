import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.auth_routes import CodigoMfaInput, confirmar_mfa
from app.auth import hash_token
from app.models import UsuarioOperacoes
from app.permissions import PERFIS_MFA_OBRIGATORIO, permissoes_do_perfil
from app.security_ext import codigo_totp, proteger_segredo
from app.web import __path__ as _web_path
from tests.conftest import FakeSession, usuario_teste


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/auth/mfa/confirmar",
            "headers": [(b"x-csrf-token", b"csrf-teste")],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_perfis_privilegiados_exigem_mfa() -> None:
    assert PERFIS_MFA_OBRIGATORIO == {"administrador", "ceo", "tech", "supervisor", "financeiro"}


def test_confirmar_mfa_retorna_destino_do_perfil() -> None:
    segredo = "JBSWY3DPEHPK3PXP"
    registro = UsuarioOperacoes(
        id=1,
        organizacao_id=1,
        nome="Financeiro",
        usuario="financeiro",
        email="financeiro@teste.local",
        perfil="financeiro",
        senha_hash="",
        mfa_ativo=False,
        mfa_segredo=proteger_segredo(segredo),
        criado_por="teste",
    )

    class SessionComUsuario(FakeSession):
        async def get(self, *_args, **_kwargs):
            return registro

    usuario = usuario_teste("financeiro", set(permissoes_do_perfil("financeiro")))
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    session = SessionComUsuario()
    session.info = {"organizacao_id": 1}
    resposta = asyncio.run(
        confirmar_mfa(
            CodigoMfaInput(codigo=codigo_totp(segredo)),
            _request(),
            usuario,
            session,
        )
    )
    assert resposta["status"] == "ativo"
    assert resposta["destino"] == "/admin/financeiro"
    assert registro.mfa_ativo is True


def test_confirmar_mfa_com_codigo_invalido_nao_ativa() -> None:
    registro = UsuarioOperacoes(
        id=1,
        organizacao_id=1,
        nome="Admin",
        usuario="admin",
        email="admin@teste.local",
        perfil="administrador",
        senha_hash="",
        mfa_ativo=False,
        mfa_segredo=proteger_segredo("JBSWY3DPEHPK3PXP"),
        criado_por="teste",
    )

    class SessionComUsuario(FakeSession):
        async def get(self, *_args, **_kwargs):
            return registro

    session = SessionComUsuario()
    session.info = {"organizacao_id": 1}
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    with pytest.raises(HTTPException) as erro:
        asyncio.run(confirmar_mfa(CodigoMfaInput(codigo="000000"), _request(), usuario, session))
    assert erro.value.status_code == 400
    assert registro.mfa_ativo is False


def test_pagina_configurar_mfa_reaproveita_endpoints_existentes() -> None:
    web = Path(_web_path[0])
    pagina = (web / "configurar-mfa.html").read_text(encoding="utf-8")
    script = (web / "static" / "auth-pages.js").read_text(encoding="utf-8")
    assert 'id="mfa-setup-panel"' in pagina
    assert 'id="mfa-setup-recovery"' in pagina
    assert "/v1/auth/mfa/iniciar" in script
    assert "/v1/auth/mfa/confirmar" in script

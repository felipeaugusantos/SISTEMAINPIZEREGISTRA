import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.auth_routes import CodigoMfaInput, confirmar_mfa
from app.auth import hash_token, obter_usuario_atual
from app.models import Organizacao, SessaoOperacoes, UsuarioOperacoes
from app.permissions import PERFIS_MFA_OBRIGATORIO, permissoes_do_perfil
from app.security_ext import codigo_totp, proteger_segredo
from app.web import __path__ as _web_path
from tests.conftest import FakeResult, FakeSession, usuario_teste


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


def _requisicao_autenticada(path: str, token: str, method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"cookie", f"zr_session={token}".encode())],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _sessao_de_operador(*, alterar_senha: bool, mfa_ativo: bool, perfil: str = "administrador") -> tuple[SessaoOperacoes, str]:
    token = "token-de-teste"
    agora = datetime.now(UTC)
    organizacao = Organizacao(
        id=1, nome="Organizacao Teste", slug="org-teste", plano_id=1, modulos_liberados=[], status="ativa"
    )
    usuario = UsuarioOperacoes(
        id=1,
        organizacao_id=1,
        nome="Operador Novo",
        usuario="operador.novo",
        email="operador.novo@teste.local",
        perfil=perfil,
        senha_hash="",
        ativo=True,
        alterar_senha=alterar_senha,
        mfa_ativo=mfa_ativo,
        criado_por="teste",
    )
    usuario.organizacao = organizacao
    usuario.permissoes = []
    sessao = SessaoOperacoes(
        id=1,
        usuario_id=1,
        token_hash=hash_token(token),
        csrf_hash=hash_token("csrf-teste"),
        ultimo_acesso_em=agora,
        expira_em=agora + timedelta(hours=8),
        revogada_em=None,
    )
    sessao.usuario = usuario
    return sessao, token


# --- Achado do usuário (22/09/2026): operador novo com senha provisória E
# perfil que exige MFA ficava em impasse -- o gate de alterar_senha libera
# POST /v1/auth/trocar-senha, mas o gate de MFA (que roda depois, na mesma
# dependência) não liberava esse mesmo endpoint, bloqueando a troca de
# senha (pré-requisito pra sequer chegar em /configurar-mfa) com
# "Configuração de MFA obrigatória" antes de rodar. ---


def test_troca_de_senha_provisoria_nao_e_bloqueada_por_mfa_pendente() -> None:
    sessao, token = _sessao_de_operador(alterar_senha=True, mfa_ativo=False, perfil="administrador")
    session = FakeSession([FakeResult(scalar=sessao)])
    request = _requisicao_autenticada("/v1/auth/trocar-senha", token, method="POST")

    resultado = asyncio.run(obter_usuario_atual(request, session))

    assert resultado.alterar_senha is True
    assert resultado.mfa_ativo is False


def test_mfa_obrigatorio_continua_bloqueando_paths_nao_liberados() -> None:
    """A correção acima não pode afrouxar o gate de MFA de verdade -- só
    abre uma exceção pontual pra trocar-senha. Qualquer outra rota
    continua exigindo MFA configurado."""
    sessao, token = _sessao_de_operador(alterar_senha=False, mfa_ativo=False, perfil="administrador")
    session = FakeSession([FakeResult(scalar=sessao)])
    request = _requisicao_autenticada("/admin", token, method="GET")

    with pytest.raises(HTTPException) as erro:
        asyncio.run(obter_usuario_atual(request, session))
    assert erro.value.status_code == 303
    assert erro.value.headers["Location"] == "/configurar-mfa"


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
    assert 'id="mfa-setup-qrcode"' in pagina
    assert "/static/vendor/qrcode.min.js" in pagina
    assert "/v1/auth/mfa/iniciar" in script
    assert "/v1/auth/mfa/confirmar" in script
    assert "new QRCode(" in script
    assert "inicio.uri" in script

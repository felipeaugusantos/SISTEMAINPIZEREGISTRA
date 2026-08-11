import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.auth_routes import TrocarSenhaInput, trocar_senha
from app.auth import hash_senha, hash_token, verificar_senha
from app.models import EventoAuditoria, UsuarioOperacoes
from app.web import __path__ as _web_path
from tests.conftest import FakeSession, usuario_teste


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/auth/trocar-senha",
            "headers": [(b"x-csrf-token", b"csrf-teste")],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _usuario():
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    return usuario


def test_senha_fraca_retorna_mensagem_simples_e_audita_sem_senha() -> None:
    session = FakeSession()
    session.info = {"organizacao_id": 1}
    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            trocar_senha(
                TrocarSenhaInput(senha_atual="Temporaria123!", nova_senha="abcdefghijkl"),
                _request(),
                _usuario(),
                session,
            )
        )
    assert erro.value.status_code == 422
    assert "maiúscula" in erro.value.detail
    evento = next(item for item in session.adicionados if isinstance(item, EventoAuditoria))
    assert evento.acao == "TROCA_SENHA_NEGADA"
    assert evento.detalhes == {"usuario_id": 1, "motivo": "senha_fraca"}
    assert "Temporaria123!" not in str(evento.detalhes)


def test_troca_valida_atualiza_hash_e_libera_primeiro_acesso() -> None:
    registro = UsuarioOperacoes(
        id=1,
        organizacao_id=1,
        nome="Operador",
        usuario="operador",
        email="operador@teste.local",
        perfil="operador",
        senha_hash=hash_senha("Temporaria123!"),
        alterar_senha=True,
        criado_por="teste",
    )

    class SessionComUsuario(FakeSession):
        async def get(self, *_args, **_kwargs):
            return registro

    session = SessionComUsuario()
    session.info = {"organizacao_id": 1}
    resposta = asyncio.run(
        trocar_senha(
            TrocarSenhaInput(senha_atual="Temporaria123!", nova_senha="NovaSenha456!"),
            _request(),
            _usuario(),
            session,
        )
    )
    assert resposta == {"status": "ok", "destino": "/admin"}
    assert registro.alterar_senha is False
    assert verificar_senha(registro.senha_hash, "NovaSenha456!")
    assert any(
        isinstance(item, EventoAuditoria) and item.acao == "TROCA_SENHA"
        for item in session.adicionados
    )


def test_tela_exibe_requisitos_e_trata_erros_estruturados() -> None:
    from pathlib import Path

    web = Path(_web_path[0])
    pagina = (web / "alterar-senha.html").read_text(encoding="utf-8")
    script = (web / "static" / "auth-pages.js").read_text(encoding="utf-8")
    assert 'data-password-rule="special"' in pagina
    assert "mensagemValidacao" in script
    assert "avaliarSenha" in script


def test_primeiro_acesso_financeiro_redireciona_para_o_modulo() -> None:
    from app.permissions import permissoes_do_perfil

    registro = UsuarioOperacoes(
        id=1,
        organizacao_id=1,
        nome="Financeiro",
        usuario="financeiro",
        email="financeiro@teste.local",
        perfil="financeiro",
        senha_hash=hash_senha("Temporaria123!"),
        alterar_senha=True,
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
        trocar_senha(
            TrocarSenhaInput(senha_atual="Temporaria123!", nova_senha="NovaSenha456!"),
            _request(),
            usuario,
            session,
        )
    )
    assert resposta["destino"] == "/admin/financeiro"

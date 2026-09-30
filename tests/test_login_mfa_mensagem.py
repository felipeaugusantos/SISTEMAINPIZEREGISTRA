"""Achado do usuário (23/09/2026): a primeira etapa normal do login em duas
etapas (senha certa, campo de código do autenticador ainda nem existia na
tela) mostrava "Código MFA inválido ou ausente" como se fosse erro de
senha -- e cada envio nessa etapa esperada ainda contava como tentativa
falha pro bloqueio de 5 tentativas, penalizando quem usa MFA mais rápido
que quem não usa.

Corrigido em app/api/auth_routes.py::login: agora distingue três casos --
senha errada ("Usuário ou senha inválidos"), senha certa mas código ainda
não informado ("Informe o código do seu autenticador", não conta pro
bloqueio nem é auditado como falha) e senha certa com código errado
("Código MFA inválido", conta normalmente).

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real.
"""

import asyncio

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.auth_routes import LoginInput, login
from app.auth import hash_senha
from app.models import Organizacao, PlanoSaas, UsuarioOperacoes
from app.security_ext import codigo_totp, proteger_segredo, versao_chave_atual
from tests.conftest import FakeResult, FakeSession


def _request(ip: str = "10.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/auth/login",
            "headers": [],
            "client": (ip, 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _sessao(usuario: UsuarioOperacoes) -> FakeSession:
    sessao = FakeSession(
        [
            # aplicar_contexto_autenticacao() roda antes da busca do
            # usuário e, com session.info já definido, executa o
            # set_config incondicionalmente (sem checar in_transaction).
            FakeResult(),
            FakeResult(scalar=usuario),
        ]
    )
    # _auditar() lê session.info diretamente (sem hasattr) pra achar a
    # organização do evento de auditoria; sessão real ganha isso de
    # aplicar_contexto_tenant, aqui precisa vir pronto.
    sessao.info = {"organizacao_id": usuario.organizacao_id}
    return sessao


def _usuario(**overrides: object) -> UsuarioOperacoes:
    base = dict(
        id=1,
        organizacao_id=1,
        nome="Operador Teste",
        usuario="operador",
        email="operador@teste.local",
        perfil="administrador",
        senha_hash=hash_senha("Senha-Correta-123"),
        ativo=True,
        superadmin=False,
        mfa_ativo=False,
        mfa_segredo=None,
        mfa_segredo_versao=versao_chave_atual(),
        tentativas_falhas=0,
        bloqueado_ate=None,
        criado_por="teste",
    )
    base.update(overrides)
    usuario = UsuarioOperacoes(**base)
    plano = PlanoSaas(id=1, nome="Padrão", codigo="padrao", modulos=[])
    usuario.organizacao = Organizacao(id=1, nome="Org Teste", slug="org-teste", plano_id=1, plano=plano)
    usuario.permissoes = []
    return usuario


def test_senha_errada_sem_mfa_mostra_mensagem_generica() -> None:
    usuario = _usuario(mfa_ativo=False)
    dados = LoginInput(identificador="operador", senha="senha-errada")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(login(dados, _request(), Response(), _sessao(usuario)))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Usuário ou senha inválidos"
    assert usuario.tentativas_falhas == 1


def test_senha_certa_mfa_ativo_sem_codigo_pede_codigo_sem_penalizar() -> None:
    segredo = "JBSWY3DPEHPK3PXP"
    usuario = _usuario(mfa_ativo=True, mfa_segredo=proteger_segredo(segredo))
    dados = LoginInput(identificador="operador", senha="Senha-Correta-123")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(login(dados, _request(), Response(), _sessao(usuario)))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Informe o código do seu autenticador"
    # Achado principal: essa etapa esperada do fluxo não pode contar como
    # tentativa falha pro bloqueio de 5 tentativas.
    assert usuario.tentativas_falhas == 0
    assert usuario.bloqueado_ate is None


def test_senha_certa_mfa_ativo_codigo_errado_mostra_mensagem_especifica() -> None:
    segredo = "JBSWY3DPEHPK3PXP"
    usuario = _usuario(mfa_ativo=True, mfa_segredo=proteger_segredo(segredo))
    dados = LoginInput(identificador="operador", senha="Senha-Correta-123", codigo_mfa="000000")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(login(dados, _request(), Response(), _sessao(usuario)))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Código MFA inválido"
    assert usuario.tentativas_falhas == 1


def test_senha_errada_mesmo_com_mfa_ativo_nao_mostra_mensagem_de_mfa() -> None:
    segredo = "JBSWY3DPEHPK3PXP"
    usuario = _usuario(mfa_ativo=True, mfa_segredo=proteger_segredo(segredo))
    dados = LoginInput(identificador="operador", senha="senha-errada")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(login(dados, _request(), Response(), _sessao(usuario)))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Usuário ou senha inválidos"
    assert usuario.tentativas_falhas == 1


def test_senha_certa_mfa_ativo_codigo_correto_autentica() -> None:
    segredo = "JBSWY3DPEHPK3PXP"
    usuario = _usuario(mfa_ativo=True, mfa_segredo=proteger_segredo(segredo))
    dados = LoginInput(identificador="operador", senha="Senha-Correta-123", codigo_mfa=codigo_totp(segredo))
    resultado = asyncio.run(login(dados, _request(), Response(), _sessao(usuario)))
    assert resultado["usuario"]["id"] == usuario.id
    assert usuario.tentativas_falhas == 0


def test_usuario_inexistente_gasta_o_tempo_de_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enumeração por tempo (pentest, 30/09/2026): antes, com usuário inexistente,
    o Argon2 nem rodava e a resposta era muito mais rápida que a de um usuário
    real com senha errada, denunciando quais contas existem. Agora o login gasta
    o tempo de um verify mesmo sem usuário."""
    chamou = {"consumiu": False}
    monkeypatch.setattr(
        "app.api.auth_routes.consumir_tempo_hash",
        lambda: chamou.__setitem__("consumiu", True),
    )
    sessao = FakeSession([FakeResult(), FakeResult(scalar=None)])
    sessao.info = {"organizacao_id": 1}
    dados = LoginInput(identificador="nao-existe", senha="qualquer")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(login(dados, _request(), Response(), sessao))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Usuário ou senha inválidos"
    assert chamou["consumiu"] is True

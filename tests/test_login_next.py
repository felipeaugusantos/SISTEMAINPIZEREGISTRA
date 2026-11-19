"""Login com retorno ao destino de origem (19/11/2026): o painel de monitoramento
manda o navegador sem chave e sem sessão para /login?next=/tv/monitoramento e,
depois de entrar, o usuário volta ao painel. O destino pedido só vale quando é
caminho interno do próprio sistema -- redirecionamento aberto nunca é aceito.
"""

import asyncio

import pytest
from fastapi import Response
from starlette.requests import Request

from app.api.auth_routes import LoginInput, login
from app.auth import hash_senha
from app.models import Organizacao, PlanoSaas, UsuarioOperacoes
from app.security_ext import versao_chave_atual
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
    # Mesma fila mínima do test_login_mfa_mensagem.py: contexto de autenticação
    # e a busca do usuário; o resto usa os padrões tolerantes da FakeSession.
    sessao = FakeSession([FakeResult(), FakeResult(scalar=usuario)])
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


def _entrar(usuario: UsuarioOperacoes, dados: LoginInput) -> dict:
    return asyncio.run(login(dados, _request(), Response(), _sessao(usuario)))


def _dados(**extra: object) -> LoginInput:
    return LoginInput(identificador="operador", senha="Senha-Correta-123", **extra)  # type: ignore[arg-type]


def test_login_com_next_interno_volta_para_o_destino_pedido() -> None:
    resposta = _entrar(_usuario(), _dados(proximo="/tv/monitoramento"))
    assert resposta["destino"] == "/tv/monitoramento"


@pytest.mark.parametrize(
    "proximo",
    ["https://externo.example/x", "//externo.example", "\\\\externo\\x", "javascript:alert(1)", ""],
)
def test_login_com_next_externo_ou_vazio_cai_no_destino_padrao(proximo: str) -> None:
    resposta = _entrar(_usuario(), _dados(proximo=proximo))
    assert resposta["destino"] == "/admin"


def test_login_sem_next_cai_no_destino_padrao() -> None:
    resposta = _entrar(_usuario(), _dados())
    assert resposta["destino"] == "/admin"


def test_troca_de_senha_obrigatoria_ignora_next() -> None:
    resposta = _entrar(_usuario(alterar_senha=True), _dados(proximo="/tv/monitoramento"))
    assert resposta["destino"] == "/alterar-senha"

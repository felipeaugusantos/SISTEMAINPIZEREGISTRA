from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from app.api import auth_routes, usuarios
from app.auth import hash_senha, obter_usuario_atual, verificar_senha
from app.models import TokenRecuperacaoSenha, UsuarioOperacoes
from app.security_ext import validar_forca_senha
from app.settings import Settings
from tests.conftest import FakeResult, FakeSession, usuario_teste
from tests.test_mfa_obrigatorio import _requisicao_autenticada, _sessao_de_operador


class Sessao(FakeSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.info = {}
        self.travas = []

    async def execute(self, statement, *args, **kwargs):
        if "set_config" in str(statement):
            return FakeResult()
        return await super().execute(statement, *args, **kwargs)

    async def refresh(self, objeto, **kwargs):
        self.travas.append(kwargs.get("with_for_update", False))


def conta(**alteracoes):
    dados = dict(
        id=5,
        organizacao_id=1,
        nome="Operador",
        usuario="operador",
        email="operador@example.test",
        perfil="operador",
        senha_hash="anterior",
        ativo=True,
        superadmin=False,
        mfa_ativo=False,
        senha_versao=1,
        alterar_senha=False,
        permissoes=[],
        tentativas_falhas=5,
        bloqueado_ate=datetime.now(UTC) + timedelta(minutes=15),
    )
    dados.update(alteracoes)
    return UsuarioOperacoes(**dados)


def request(path="/v1/auth/login"):
    return _requisicao_autenticada(path, "token", "POST")


async def test_reset_admin_sem_email_desbloqueia_invalida_links_e_exige_troca(monkeypatch):
    monkeypatch.setattr(usuarios, "get_settings", lambda: Settings(email_enabled=False))
    alvo = conta()
    session = Sessao([FakeResult(scalar=alvo)])
    antes = datetime.now(UTC)
    resposta = await usuarios.redefinir_senha(5, session, usuario_teste())
    assert verificar_senha(alvo.senha_hash, resposta["senha_temporaria"])
    assert alvo.alterar_senha is True
    assert alvo.tentativas_falhas == 0 and alvo.bloqueado_ate is None
    assert antes + timedelta(minutes=59) < resposta["expira_em"] <= datetime.now(UTC) + timedelta(minutes=60)
    comandos = "\n".join(map(str, session.executados))
    assert "UPDATE tokens_recuperacao_senha" in comandos
    assert "tokens_recuperacao_senha.usuario_id" in comandos
    assert "UPDATE sessoes_operacoes" in comandos
    assert session.commits == 1
    assert resposta["senha_temporaria"] not in str(session.adicionados)


async def test_reset_nao_reativa_conta_desativada():
    alvo = conta(ativo=False)
    session = Sessao([FakeResult(scalar=alvo)])
    with pytest.raises(HTTPException) as erro:
        await usuarios.redefinir_senha(5, session, usuario_teste())
    assert erro.value.status_code == 409
    assert alvo.senha_hash == "anterior" and session.commits == 0


def test_senhas_temporarias_sempre_atendem_a_politica():
    for _ in range(100):
        senha = usuarios._temporaria()
        assert len(senha) == 18
        validar_forca_senha(senha)


async def test_senha_temporaria_expirada_nao_inicia_sessao_nem_bloqueia_conta():
    alvo = conta(
        senha_hash=hash_senha("Temporaria-123!"),
        bloqueado_ate=None,
        senha_temporaria_expira_em=datetime.now(UTC) - timedelta(seconds=1),
    )
    session = Sessao([FakeResult(scalar=alvo)])
    with pytest.raises(HTTPException) as erro:
        await auth_routes.login(
            auth_routes.LoginInput(identificador="operador", senha="Temporaria-123!"), request(), Response(), session
        )
    assert erro.value.status_code == 401 and "expirada" in erro.value.detail
    assert alvo.bloqueado_ate is None
    assert not any(type(obj).__name__ == "SessaoOperacoes" for obj in session.adicionados)


async def test_sessao_provisoria_tambem_expira():
    sessao, token = _sessao_de_operador(alterar_senha=True, mfa_ativo=False)
    sessao.usuario.senha_temporaria_expira_em = datetime.now(UTC) - timedelta(seconds=1)
    session = Sessao([FakeResult(scalar=sessao)])
    with pytest.raises(HTTPException) as erro:
        await obter_usuario_atual(_requisicao_autenticada("/v1/auth/trocar-senha", token), session)
    assert erro.value.status_code == 401
    assert sessao.motivo_revogacao == "senha_temporaria_expirada"


@pytest.mark.parametrize("email", ["existe@example.test", "naoexiste@example.test"])
async def test_email_desativado_em_producao_orienta_painel_sem_consultar_contas(monkeypatch, email):
    monkeypatch.setattr(
        auth_routes,
        "get_settings",
        lambda: SimpleNamespace(app_env="production", email_enabled=False, app_public_url="https://app.example.test"),
    )
    session = Sessao()
    with pytest.raises(HTTPException) as erro:
        await auth_routes.solicitar_recuperacao(auth_routes.RecuperacaoInput(email=email), request(), session)
    assert erro.value.status_code == 503 and "painel de usuários" in erro.value.detail
    assert session.executados == [] and session.adicionados == []


async def test_falha_smtp_nao_expoe_existencia_da_conta_nem_token(monkeypatch):
    settings = Settings(email_enabled=True)
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_routes, "enviar_recuperacao_senha", AsyncMock(side_effect=OSError("SMTP indisponivel")))
    monkeypatch.setattr(auth_routes, "nome_escritorio_para_email", AsyncMock(return_value=None))
    session = Sessao([FakeResult(scalar=conta())])
    existente = await auth_routes.solicitar_recuperacao(
        auth_routes.RecuperacaoInput(email="operador@example.test"), request(), session
    )
    inexistente = await auth_routes.solicitar_recuperacao(
        auth_routes.RecuperacaoInput(email="ninguem@example.test"), request(), Sessao([FakeResult()])
    )
    assert existente == inexistente
    assert "administrador" in existente["mensagem"]
    assert "token_teste_local" not in existente
    assert session.travas == [True]


async def test_recuperacao_local_sem_email_preserva_token_de_teste(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_settings", lambda: Settings(email_enabled=False))
    monkeypatch.setattr(auth_routes, "nome_escritorio_para_email", AsyncMock(return_value=None))
    session = Sessao([FakeResult(scalar=conta())])
    resposta = await auth_routes.solicitar_recuperacao(
        auth_routes.RecuperacaoInput(email="operador@example.test"), request(), session
    )
    assert len(resposta["token_teste_local"]) >= 48
    assert any(isinstance(item, TokenRecuperacaoSenha) for item in session.adicionados)


@pytest.mark.parametrize("ativo,consumido", [(False, False), (True, False), (True, True)])
async def test_link_cancelado_ou_conta_inativa_nao_troca_senha(monkeypatch, ativo, consumido):
    alvo = conta(ativo=ativo)
    item = TokenRecuperacaoSenha(id=9, usuario_id=alvo.id)
    session = Sessao([FakeResult(scalar=item), FakeResult(itens=[(9,)] if consumido else [])], objetos_get=[alvo])
    dados = auth_routes.RedefinirSenhaInput(token="x" * 64, nova_senha="NovaSenha-456!y")
    if not ativo or not consumido:
        with pytest.raises(HTTPException) as erro:
            await auth_routes.redefinir_senha(dados, request(), session)
        assert erro.value.status_code == 400
        assert alvo.senha_hash == "anterior" and session.commits == 0
    else:
        await auth_routes.redefinir_senha(dados, request(), session)
        assert verificar_senha(alvo.senha_hash, dados.nova_senha)
        assert alvo.bloqueado_ate is None and alvo.tentativas_falhas == 0
        assert alvo.senha_temporaria_expira_em is None and alvo.alterar_senha is False
    assert session.travas == [True]


async def test_troca_normal_cancela_links_e_remove_prazo_temporario():
    alvo = conta(
        id=1,
        senha_hash=hash_senha("SenhaAtual-123!x"),
        senha_temporaria_expira_em=datetime.now(UTC) + timedelta(minutes=30),
    )
    session = Sessao(objetos_get=[alvo])
    # A dependência autenticada já validou CSRF. Aqui exercitamos a rota com seu hash correspondente.
    from dataclasses import replace

    from app.auth import hash_token

    usuario = replace(usuario_teste(), csrf_hash=hash_token("csrf"))
    requisicao = request("/v1/auth/trocar-senha")
    requisicao.scope["headers"].append((b"x-csrf-token", b"csrf"))
    await auth_routes.trocar_senha(
        auth_routes.TrocarSenhaInput(senha_atual="SenhaAtual-123!x", nova_senha="NovaSenha-456!y"),
        requisicao,
        usuario,
        session,
    )
    assert alvo.senha_temporaria_expira_em is None
    assert any("UPDATE tokens_recuperacao_senha" in str(stmt) for stmt in session.executados)


@pytest.mark.parametrize(
    "url", ["http://localhost:8000", "https://127.0.0.1", "https://[invalido", "https://app.example.test#token=x"]
)
async def test_producao_nao_emite_links_com_endereco_invalido(monkeypatch, url):
    monkeypatch.setattr(
        auth_routes,
        "get_settings",
        lambda: SimpleNamespace(app_env="production", email_enabled=True, app_public_url=url),
    )
    session = Sessao()
    with pytest.raises(HTTPException) as erro:
        await auth_routes.solicitar_recuperacao(
            auth_routes.RecuperacaoInput(email="operador@example.test"), request(), session
        )
    assert erro.value.status_code == 503
    assert session.adicionados == [] and session.executados == []


@pytest.mark.parametrize("minutos", [0, 4, 1441])
def test_prazo_temporario_precisa_ser_util_e_limitado(minutos):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(temporary_password_minutes=minutos)


async def test_recuperacao_emergencial_invalida_acessos_e_audita(monkeypatch):
    from app.cli import redefinir_admin

    class SessaoCLI(Sessao):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    session = SessaoCLI([FakeResult(), FakeResult(scalar=5)])
    monkeypatch.setenv("NOVA_SENHA", "NovaSenha-456!y")
    monkeypatch.setenv("ADMIN_USUARIO", "operador")
    monkeypatch.setattr(redefinir_admin, "session_factory", lambda: session)
    await redefinir_admin.executar()
    comandos = "\n".join(map(str, session.executados))
    assert "senha_temporaria_expira_em = NULL" in comandos
    assert "UPDATE tokens_recuperacao_senha" in comandos
    assert "UPDATE sessoes_operacoes" in comandos
    assert "INSERT INTO eventos_auditoria" in comandos
    assert "NovaSenha-456!y" not in comandos
    assert session.commits == 1

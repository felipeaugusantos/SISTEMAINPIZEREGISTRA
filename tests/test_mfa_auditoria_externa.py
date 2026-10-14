import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, Request, Response
from fastapi.testclient import TestClient

import app.api.social_auth as modulo_social_auth
from app.api.social_auth import CodigoMfaSocialInput, concluir_mfa
from app.auth import consumir_codigo_recuperacao, hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import TentativaOAuth, UsuarioOperacoes
from app.security_ext import codigo_totp, gerar_segredo_totp, proteger_segredo
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

# Auditoria externa de 13/10/2026: A01 (substituir MFA sem provar o fator atual),
# A02 (MFA social sem limite próprio e conclusão repetível) e A07 (código de
# recuperação sem consumo atômico).

URL_INICIAR = "/v1/auth/mfa/iniciar"
CABECALHO = {"X-CSRF-Token": "tok"}


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


def _registro(mfa_ativo: bool, segredo: str | None = None) -> UsuarioOperacoes:
    return UsuarioOperacoes(
        id=1,
        organizacao_id=1,
        nome="Admin Teste",
        usuario="admin",
        email="admin@teste.local",
        perfil="administrador",
        senha_hash="hash",
        ativo=True,
        superadmin=False,
        mfa_ativo=mfa_ativo,
        mfa_segredo=proteger_segredo(segredo) if segredo else None,
        mfa_segredo_versao=1 if segredo else None,
        codigos_recuperacao=[],
        permissoes=[],
    )


class _SessaoComTenant(FakeSession):
    # _auditar lê o tenant de session.info, que a autenticação real preenche.
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.info = {"organizacao_id": 1}


def _cliente(registro: UsuarioOperacoes) -> TestClient:
    async def _sessao():
        yield _SessaoComTenant([], objetos_get=[registro])

    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        replace(usuario_teste(), csrf_hash=hash_token("tok"))
    )
    return TestClient(app, raise_server_exceptions=False)


def test_a01_substituir_mfa_ativo_sem_codigo_e_recusado_e_nada_muda() -> None:
    registro = _registro(True, gerar_segredo_totp())
    antigo = registro.mfa_segredo
    resposta = _cliente(registro).post(URL_INICIAR, json={}, headers=CABECALHO)
    assert resposta.status_code == 403
    assert registro.mfa_ativo is True
    assert registro.mfa_segredo == antigo


def test_a01_codigo_errado_tambem_e_recusado() -> None:
    registro = _registro(True, gerar_segredo_totp())
    antigo = registro.mfa_segredo
    resposta = _cliente(registro).post(URL_INICIAR, json={"codigo": "000000"}, headers=CABECALHO)
    assert resposta.status_code == 403
    assert registro.mfa_ativo is True
    assert registro.mfa_segredo == antigo


def test_a01_com_o_codigo_atual_a_substituicao_e_permitida() -> None:
    segredo = gerar_segredo_totp()
    registro = _registro(True, segredo)
    antigo = registro.mfa_segredo
    resposta = _cliente(registro).post(URL_INICIAR, json={"codigo": codigo_totp(segredo)}, headers=CABECALHO)
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "aguardando_confirmacao"
    # S03: o fator ativo segue valendo; o segredo novo fica pendente até ser confirmado.
    assert registro.mfa_ativo is True
    assert registro.mfa_segredo == antigo
    assert registro.mfa_segredo_pendente is not None


def test_a01_primeira_configuracao_nao_exige_codigo() -> None:
    registro = _registro(False)
    resposta = _cliente(registro).post(URL_INICIAR, json={}, headers=CABECALHO)
    assert resposta.status_code == 200
    assert registro.mfa_segredo is not None


def _tentativa(mfa_hash: str) -> TentativaOAuth:
    return TentativaOAuth(
        id=9,
        provedor="google",
        state_hash="state-hash",
        browser_token_hash="browser-hash",
        nonce="nonce",
        code_verifier="verifier",
        modo="login",
        usuario_id=7,
        destino="/admin",
        mfa_token_hash=mfa_hash,
        expira_em=datetime.now(UTC) + timedelta(minutes=5),
    )


def _usuario_social() -> UsuarioOperacoes:
    return UsuarioOperacoes(
        id=7,
        organizacao_id=3,
        nome="Operador",
        usuario="operador",
        email="operador@teste.local",
        perfil="operador",
        senha_hash="hash",
        ativo=True,
        superadmin=False,
        mfa_ativo=True,
        mfa_segredo=None,
        mfa_segredo_versao=modulo_social_auth.versao_chave_atual(),
        codigos_recuperacao=[],
        permissoes=[],
    )


def _requisicao(cookie: str | None) -> Request:
    cabecalhos = [(b"cookie", f"{modulo_social_auth.MFA_COOKIE}={cookie}".encode())] if cookie else []
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/auth/social/mfa",
            "headers": cabecalhos,
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )


def _concluir(session: FakeSession, cookie: str | None = "desafio") -> dict:
    return asyncio.run(concluir_mfa(CodigoMfaSocialInput(codigo="123456"), _requisicao(cookie), Response(), session))


def _stubs(monkeypatch: pytest.MonkeyPatch, totp_valido: bool) -> None:
    async def _consumir_totp(*_a, **_k):
        return totp_valido

    monkeypatch.setattr(modulo_social_auth, "consumir_totp", _consumir_totp)
    monkeypatch.setattr(modulo_social_auth, "revelar_segredo", lambda *_a, **_k: "")


def test_a02_desafio_e_queimado_apos_cinco_tentativas_erradas(monkeypatch: pytest.MonkeyPatch) -> None:
    _stubs(monkeypatch, totp_valido=False)
    tentativa = _tentativa(hash_token("desafio"))
    codigos = []
    for _ in range(6):
        sessao = FakeSession([FakeResult(scalar=tentativa), FakeResult(scalar=_usuario_social())])
        try:
            _concluir(sessao)
            codigos.append(200)
        except HTTPException as erro:
            codigos.append(erro.status_code)
    assert codigos == [401, 401, 401, 401, 401, 429]
    # O desafio foi queimado com um DELETE condicional (e confirmado) na 6ª tentativa.
    assert any(str(instrucao).lstrip().upper().startswith("DELETE") for instrucao in sessao.executados)
    assert sessao.commits == 1


def test_a02_limite_por_ip_na_conclusao_do_mfa_social(monkeypatch: pytest.MonkeyPatch) -> None:
    _stubs(monkeypatch, totp_valido=False)
    codigos = []
    for _ in range(11):
        with pytest.raises(HTTPException) as erro:
            _concluir(FakeSession([]), cookie=None)
        codigos.append(erro.value.status_code)
    assert codigos == [401] * 10 + [429]


def test_a02_desafio_nao_conclui_duas_vezes(monkeypatch: pytest.MonkeyPatch) -> None:
    _stubs(monkeypatch, totp_valido=True)
    tentativa = _tentativa(hash_token("desafio"))
    # O UPDATE condicional não afetou nenhuma linha: outra conclusão já reivindicou o desafio.
    sessao = FakeSession([FakeResult(scalar=tentativa), FakeResult(scalar=_usuario_social()), FakeResult(rowcount=0)])
    with pytest.raises(HTTPException) as erro:
        _concluir(sessao)
    assert erro.value.status_code == 401


class _SessaoComTrava(FakeSession):
    def __init__(self) -> None:
        super().__init__([])
        self.refreshes: list[dict] = []

    async def refresh(self, obj, *args, **kwargs) -> None:  # noqa: ANN001
        self.refreshes.append(kwargs)


def test_a07_consumo_trava_a_linha_e_remove_o_codigo() -> None:
    usuario = _usuario_social()
    codigo = hash_token("ABC123")
    usuario.codigos_recuperacao = [codigo, hash_token("OUTRO")]
    sessao = _SessaoComTrava()
    assert asyncio.run(consumir_codigo_recuperacao(sessao, usuario, codigo)) is True
    assert usuario.codigos_recuperacao == [hash_token("OUTRO")]
    assert sessao.refreshes
    assert sessao.refreshes[0]["with_for_update"] is True
    assert sessao.refreshes[0]["attribute_names"] == ["codigos_recuperacao"]


def test_a07_codigo_ja_consumido_por_outra_transacao_nao_vale_de_novo() -> None:
    usuario = _usuario_social()
    usuario.codigos_recuperacao = []  # relido sob a trava: o outro login já o consumiu
    assert asyncio.run(consumir_codigo_recuperacao(_SessaoComTrava(), usuario, hash_token("ABC123"))) is False

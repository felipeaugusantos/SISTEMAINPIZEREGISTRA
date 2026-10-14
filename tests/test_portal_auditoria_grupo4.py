import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks, HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

import app.api.portal_cliente as modulo_portal
from app.api.portal_cliente import (
    ClienteLogin,
    RecuperacaoRedefinicao,
    RecuperacaoSolicitacao,
    login_cliente,
    redefinir_acesso_portal,
    solicitar_recuperacao_portal,
)
from app.auth import hash_senha, hash_token
from app.database import get_session
from app.main import app
from app.models import ClientePortal, RecuperacaoClientePortal
from tests.conftest import FakeResult, FakeSession
from tests.test_portal_cliente import _cliente, _request

# Auditoria externa de 14/10/2026: S06 (tempo do login do portal), S07 (Cache-Control das
# respostas do portal) e S08 (links de recuperação irmãos sobreviviam à redefinição).

TOKEN = "token-valido-com-tamanho-suficiente"


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


# --- S06 ---


def _login_com(monkeypatch: pytest.MonkeyPatch, cliente: ClientePortal | None) -> dict:
    chamadas = {"hash_descartavel": 0}

    def _contar() -> None:
        chamadas["hash_descartavel"] += 1

    monkeypatch.setattr(modulo_portal, "consumir_tempo_hash", _contar)
    sessao = FakeSession([FakeResult(scalar=cliente)])
    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            login_cliente(
                ClienteLogin(email="cliente@empresa.com.br", senha="senha-errada"), _request(), Response(), sessao
            )
        )
    assert erro.value.status_code == 401
    assert erro.value.detail == "E-mail ou senha inválidos"
    return chamadas


def _cliente_com_senha(**extras) -> ClientePortal:
    cliente = ClientePortal(
        id=1,
        organizacao_id=1,
        lead_id=9,
        email="cliente@empresa.com.br",
        senha_hash=hash_senha("Senha-Correta-123"),
        ativo=True,
        tentativas_falhas=0,
    )
    for campo, valor in extras.items():
        setattr(cliente, campo, valor)
    return cliente


def test_s06_conta_inexistente_gasta_o_tempo_de_um_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _login_com(monkeypatch, None)["hash_descartavel"] == 1


def test_s06_conta_inativa_ou_bloqueada_tambem_gasta_o_tempo_de_um_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _login_com(monkeypatch, _cliente_com_senha(ativo=False))["hash_descartavel"] == 1
    assert _login_com(monkeypatch, _cliente_com_senha(bloqueado_em=datetime.now(UTC)))["hash_descartavel"] == 1
    bloqueada = _cliente_com_senha(bloqueado_ate=datetime.now(UTC) + timedelta(minutes=5))
    assert _login_com(monkeypatch, bloqueada)["hash_descartavel"] == 1


def test_s06_conta_valida_com_senha_errada_ja_gasta_o_hash_real_sem_descartavel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _login_com(monkeypatch, _cliente_com_senha())["hash_descartavel"] == 0


# --- S07 ---


def _cliente_http() -> TestClient:
    async def _sessao():
        yield FakeSession([])

    app.dependency_overrides[get_session] = _sessao
    return TestClient(app, raise_server_exceptions=False)


def test_s07_respostas_do_portal_nao_ficam_em_cache() -> None:
    cliente = _cliente_http()
    resposta = cliente.get("/v1/portal/me")  # sem sessão: 401, e mesmo o erro não pode ser guardado
    assert resposta.status_code == 401
    assert resposta.headers["cache-control"] == "private, no-store"
    pagina = cliente.get("/portal")
    assert pagina.status_code == 200
    assert pagina.headers["cache-control"] == "private, no-store"


def test_s07_arquivos_estaticos_publicos_continuam_sem_essa_regra() -> None:
    resposta = _cliente_http().get("/static/styles.css")
    assert resposta.status_code == 200
    assert resposta.headers.get("cache-control") != "private, no-store"


def test_s07_endpoint_que_escolhe_o_proprio_cache_privado_mantem_a_escolha() -> None:
    from starlette.requests import Request

    from app.observability import _cabecalhos_seguranca

    resposta = Response(headers={"Cache-Control": "private, max-age=86400, immutable"})
    requisicao = Request({"type": "http", "method": "GET", "path": "/v1/portal/logo-cliente", "headers": []})
    _cabecalhos_seguranca(resposta, requisicao)
    assert resposta.headers["Cache-Control"] == "private, max-age=86400, immutable"


# --- S08 ---


def _atualizacoes_de_recuperacao(sessao: FakeSession) -> list[str]:
    return [
        str(c.compile(dialect=postgresql.dialect()))
        for c in sessao.executados
        if "UPDATE recuperacoes_clientes_portal" in str(c)
    ]


def test_s08_pedir_novo_link_invalida_os_anteriores_pendentes() -> None:
    sessao = FakeSession([FakeResult(scalar=_cliente())])
    asyncio.run(
        solicitar_recuperacao_portal(
            RecuperacaoSolicitacao(email="cliente@empresa.com.br"), _request(), sessao, BackgroundTasks()
        )
    )
    (atualizacao,) = _atualizacoes_de_recuperacao(sessao)
    assert "usado_em IS NULL" in atualizacao and "cliente_id" in atualizacao
    # o link novo é criado DEPOIS de invalidar os antigos (senão invalidaria a si mesmo)
    assert any(isinstance(obj, RecuperacaoClientePortal) for obj in sessao.adicionados)
    ordem = [type(c).__name__ for c in sessao.executados]
    assert ordem.index("Update") > ordem.index("Select")


def test_s08_redefinir_com_um_link_invalida_os_outros_da_mesma_conta() -> None:
    cliente = _cliente()
    registro = RecuperacaoClientePortal(
        id=1,
        cliente_id=cliente.id,
        token_hash=hash_token(TOKEN),
        expira_em=datetime(2099, 1, 1, tzinfo=UTC),
    )
    # SELECT do token, UPDATE ... RETURNING do consumo, SELECT das sessões; depois a invalidação dos irmãos
    sessao = FakeSession(
        [FakeResult(scalar=registro), FakeResult(itens=[(registro.id,)]), FakeResult(itens=[])],
        objetos_get=[cliente],
    )
    resultado = asyncio.run(
        redefinir_acesso_portal(
            RecuperacaoRedefinicao(token=TOKEN, nova_senha="Senha-Correta-123"), _request(), Response(), sessao
        )
    )
    assert resultado["status"] == "ok"
    atualizacoes = _atualizacoes_de_recuperacao(sessao)
    assert len(atualizacoes) == 2  # o consumo do próprio token e a invalidação dos demais
    assert "usado_em IS NULL" in atualizacoes[1] and "cliente_id" in atualizacoes[1]
    assert "recuperacoes_clientes_portal.id =" not in atualizacoes[1]  # atinge TODOS os pendentes da conta


def test_s08_a_conta_e_travada_antes_de_consumir_o_token() -> None:
    # Duas redefinições da mesma conta com tokens diferentes precisam se serializar: o código trava a
    # linha do cliente (FOR UPDATE) ANTES do consumo; quem espera encontra os irmãos já invalidados.
    from pathlib import Path

    fonte = Path("app/api/portal_cliente.py").read_text(encoding="utf-8")
    corpo = fonte.split("async def redefinir_acesso_portal(", 1)[1].split("\n@router", 1)[0]
    assert corpo.index("with_for_update=True") < corpo.index("update(RecuperacaoClientePortal)")

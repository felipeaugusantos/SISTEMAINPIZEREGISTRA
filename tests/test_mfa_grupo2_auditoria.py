import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.auth_routes import CodigoMfaInput, confirmar_mfa
from app.auth import consumir_totp, hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import EventoAuditoria, UsuarioOperacoes
from app.security_ext import (
    codigo_totp,
    gerar_segredo_totp,
    passo_totp,
    proteger_segredo,
    revelar_segredo,
    validar_totp,
)
from tests.conftest import FakeSession, auth_override, usuario_teste

# Auditoria externa de 14/10/2026: S02 (desativar MFA sem limite por conta), S03 (troca do
# autenticador derrubava o fator antes de confirmar o novo) e S04 (TOTP reutilizável).

CABECALHO = {"X-CSRF-Token": "tok"}
INSTANTE = 1_700_000_000


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


class _Sessao(FakeSession):
    def __init__(self, registro: UsuarioOperacoes | None = None) -> None:
        super().__init__([], objetos_get=[registro] if registro else [])
        self.info = {"organizacao_id": 1}


def _registro(segredo: str | None, *, ativo: bool = True) -> UsuarioOperacoes:
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
        mfa_ativo=ativo,
        mfa_segredo=proteger_segredo(segredo) if segredo else None,
        mfa_segredo_versao=1 if segredo else None,
        codigos_recuperacao=["antigo"],
        permissoes=[],
    )


def _cliente(registro: UsuarioOperacoes) -> tuple[TestClient, _Sessao]:
    sessao = _Sessao(registro)

    async def _dep():
        yield sessao

    app.dependency_overrides[get_session] = _dep
    app.dependency_overrides[obter_usuario_atual] = auth_override(replace(usuario_teste(), csrf_hash=hash_token("tok")))
    return TestClient(app, raise_server_exceptions=False), sessao


def _auditorias(sessao: FakeSession) -> list:
    return [obj for obj in sessao.adicionados if isinstance(obj, EventoAuditoria)]


# --- S04: TOTP de uso único ---


def test_s04_passo_do_totp_corresponde_a_janela_aceita() -> None:
    segredo = gerar_segredo_totp()
    codigo = codigo_totp(segredo, INSTANTE)
    assert passo_totp(segredo, codigo, INSTANTE) == INSTANTE // 30
    assert passo_totp(segredo, codigo, INSTANTE + 30) == INSTANTE // 30  # janela seguinte ainda aceita
    assert passo_totp(segredo, codigo, INSTANTE + 120) is None
    assert passo_totp(segredo, "000000", INSTANTE) is None
    assert validar_totp(segredo, codigo, INSTANTE)
    assert passo_totp(segredo, "ação", INSTANTE) is None  # entrada não ASCII não derruba a validação


def test_s04_o_mesmo_codigo_so_vale_uma_vez() -> None:
    segredo = gerar_segredo_totp()
    registro = _registro(segredo)
    sessao = _Sessao(registro)
    codigo = codigo_totp(segredo)
    assert asyncio.run(consumir_totp(sessao, registro, segredo, codigo)) is True
    assert registro.mfa_ultimo_passo is not None
    assert asyncio.run(consumir_totp(sessao, registro, segredo, codigo)) is False


def test_s04_codigo_de_passo_anterior_ao_ja_usado_e_recusado() -> None:
    segredo = gerar_segredo_totp()
    registro = _registro(segredo)
    sessao = _Sessao(registro)
    agora = int(datetime.now(UTC).timestamp())
    registro.mfa_ultimo_passo = agora // 30 + 1  # um código da janela seguinte já foi consumido
    assert asyncio.run(consumir_totp(sessao, registro, segredo, codigo_totp(segredo, agora))) is False
    assert asyncio.run(consumir_totp(sessao, registro, segredo, codigo_totp(segredo, agora + 30))) is False


def test_s04_codigo_errado_nao_consome_nada() -> None:
    segredo = gerar_segredo_totp()
    registro = _registro(segredo)
    assert asyncio.run(consumir_totp(_Sessao(registro), registro, segredo, "000000")) is False
    assert registro.mfa_ultimo_passo is None


# --- S02: desativar MFA ---


def test_s02_desativar_com_codigo_valido_encerra_outras_sessoes() -> None:
    segredo = gerar_segredo_totp()
    registro = _registro(segredo)
    cliente, sessao = _cliente(registro)
    resposta = cliente.request("DELETE", "/v1/auth/mfa", json={"codigo": codigo_totp(segredo)}, headers=CABECALHO)
    assert resposta.status_code == 200
    assert registro.mfa_ativo is False and registro.mfa_segredo is None
    assert registro.mfa_ultimo_passo is None and registro.codigos_recuperacao == []
    revogacoes = [c for c in sessao.executados if hasattr(c, "compile") and "mfa_alterado" in str(c.compile().params)]
    assert len(revogacoes) == 1


def test_s02_desativar_tem_limite_por_conta_e_audita_as_negadas() -> None:
    segredo = gerar_segredo_totp()
    registro = _registro(segredo)
    cliente, sessao = _cliente(registro)
    codigos = [
        cliente.request("DELETE", "/v1/auth/mfa", json={"codigo": "000000"}, headers=CABECALHO).status_code
        for _ in range(12)
    ]
    assert codigos[:10] == [400] * 10
    assert 429 in codigos[10:]
    assert registro.mfa_ativo is True  # nada foi desativado
    negadas = [e for e in _auditorias(sessao) if e.acao == "MFA_DESATIV_NEGADO"]
    assert len(negadas) == 10 and all(not e.sucesso for e in negadas)


def test_s02_codigo_repetido_nao_desativa_o_mfa() -> None:
    segredo = gerar_segredo_totp()
    registro = _registro(segredo)
    registro.mfa_ultimo_passo = int(datetime.now(UTC).timestamp()) // 30 + 1  # já consumido
    cliente, _ = _cliente(registro)
    resposta = cliente.request("DELETE", "/v1/auth/mfa", json={"codigo": codigo_totp(segredo)}, headers=CABECALHO)
    assert resposta.status_code == 400
    assert registro.mfa_ativo is True


# --- S03: troca do autenticador com segredo pendente ---


def test_s03_iniciar_a_troca_mantem_o_fator_ativo_e_guarda_o_novo_como_pendente() -> None:
    segredo = gerar_segredo_totp()
    registro = _registro(segredo)
    antigo = registro.mfa_segredo
    cliente, _ = _cliente(registro)
    resposta = cliente.post("/v1/auth/mfa/iniciar", json={"codigo": codigo_totp(segredo)}, headers=CABECALHO)
    assert resposta.status_code == 200
    assert registro.mfa_ativo is True and registro.mfa_segredo == antigo
    assert revelar_segredo(registro.mfa_segredo_pendente) == resposta.json()["segredo"]
    assert registro.mfa_pendente_expira_em > datetime.now(UTC)


def _confirmar(registro: UsuarioOperacoes, codigo: str):
    sessao = _Sessao(registro)
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))

    from tests.test_mfa_obrigatorio import _request

    resultado = asyncio.run(confirmar_mfa(CodigoMfaInput(codigo=codigo), _request(), usuario, sessao))
    return resultado, sessao


def test_s03_confirmar_a_troca_substitui_o_fator_e_renova_os_codigos() -> None:
    antigo, novo = gerar_segredo_totp(), gerar_segredo_totp()
    registro = _registro(antigo)
    registro.mfa_segredo_pendente = proteger_segredo(novo)
    registro.mfa_pendente_expira_em = datetime.now(UTC) + timedelta(minutes=10)
    resultado, _ = _confirmar(registro, codigo_totp(novo))
    assert resultado["status"] == "ativo"
    assert revelar_segredo(registro.mfa_segredo) == novo
    assert registro.mfa_segredo_pendente is None and registro.mfa_pendente_expira_em is None
    assert registro.mfa_ativo is True
    assert registro.codigos_recuperacao != ["antigo"] and len(registro.codigos_recuperacao) == 8
    assert registro.mfa_ultimo_passo is not None


def test_s03_codigo_do_fator_antigo_nao_confirma_a_troca() -> None:
    antigo, novo = gerar_segredo_totp(), gerar_segredo_totp()
    registro = _registro(antigo)
    registro.mfa_segredo_pendente = proteger_segredo(novo)
    registro.mfa_pendente_expira_em = datetime.now(UTC) + timedelta(minutes=10)
    with pytest.raises(HTTPException) as erro:
        _confirmar(registro, codigo_totp(antigo))
    assert erro.value.status_code == 400
    assert revelar_segredo(registro.mfa_segredo) == antigo  # nada mudou


def test_s03_troca_pendente_expirada_nao_substitui_o_fator() -> None:
    antigo, novo = gerar_segredo_totp(), gerar_segredo_totp()
    registro = _registro(antigo)
    registro.mfa_segredo_pendente = proteger_segredo(novo)
    registro.mfa_pendente_expira_em = datetime.now(UTC) - timedelta(minutes=1)
    with pytest.raises(HTTPException) as erro:
        _confirmar(registro, codigo_totp(novo))
    assert erro.value.status_code == 400
    assert revelar_segredo(registro.mfa_segredo) == antigo


def test_s03_abandonar_a_troca_nao_muda_o_login() -> None:
    # Só iniciar (sem confirmar): o login continua exigindo o fator antigo.
    segredo = gerar_segredo_totp()
    registro = _registro(segredo)
    cliente, _ = _cliente(registro)
    cliente.post("/v1/auth/mfa/iniciar", json={"codigo": codigo_totp(segredo)}, headers=CABECALHO)
    assert registro.mfa_ativo is True
    assert passo_totp(revelar_segredo(registro.mfa_segredo), codigo_totp(segredo)) is not None

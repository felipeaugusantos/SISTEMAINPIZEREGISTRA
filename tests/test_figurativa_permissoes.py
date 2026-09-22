import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.figurativa import BenchmarkEntrada, benchmark_figurativo
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.permissions import permissoes_do_perfil
from tests.conftest import FakeSession, auth_override, usuario_teste

# --- Achado médio da Fase 11 (auditoria da busca figurativa, 22/09/2026):
# POST /v1/admin/figurativa/benchmark (decide se um modelo pode ser
# publicado) e POST /v1/admin/figurativa/validacoes-humanas (registra uma
# decisão legal/técnica sobre anterioridade figurativa) exigiam só
# leads.view, a mesma permissão de qualquer perfil comercial -- alinhado
# aos módulos irmãos (Validação/Risco), que exigem validation.review/
# risk.review para escrever. ---


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/figurativa/benchmark",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


async def _sessao() -> FakeSession:
    yield FakeSession()


def test_comercial_nao_pode_rodar_benchmark_de_modelo() -> None:
    usuario = usuario_teste("comercial", set(permissoes_do_perfil("comercial")))
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/figurativa/benchmark",
            json={"casos": [{"relevantes": ["900000001"], "retornados": ["900000001"]}]},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 403


def test_comercial_nao_pode_registrar_validacao_humana() -> None:
    usuario = usuario_teste("comercial", set(permissoes_do_perfil("comercial")))
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/figurativa/validacoes-humanas",
            json={"processo": "900000001", "decisao": "confirmado", "observacao": "Conferido manualmente."},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 403


def test_administrador_ainda_pode_rodar_benchmark_e_validar() -> None:
    """A correção acima não pode travar quem legitimamente tem acesso --
    administrador continua com todas as permissões."""
    usuario = usuario_teste("administrador")
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/figurativa/benchmark",
            json={"casos": [{"relevantes": ["900000001"], "retornados": ["900000001"]}]},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200


# --- Achado baixo da Fase 11: caso de benchmark malformado estourava 500
# cru (TypeError não tratado) em vez de uma mensagem clara. ---


def test_benchmark_com_relevantes_nao_hasheavel_devolve_422() -> None:
    dados = BenchmarkEntrada(casos=[{"relevantes": [{"id": 1}], "retornados": ["900000001"]}])
    usuario = usuario_teste()
    session = FakeSession()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(benchmark_figurativo(dados, _request(), session, usuario))
    assert exc_info.value.status_code == 422

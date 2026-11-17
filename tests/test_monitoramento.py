from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

# Monitoramento gerencial de erro e uso (16/11/2026): números cruzam
# organizações, então todo endpoint exige production.view + perfil tech,
# mesmo recorte da Observabilidade.

FUSO = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


def _override_session(session: FakeSession):
    async def _gen():
        yield session

    return _gen


def _sessao_tech(*resultados: FakeResult, perfil: str = "administrador") -> FakeSession:
    session = FakeSession(list(resultados))
    usuario = usuario_teste(perfil=perfil)
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return session


def test_resumo_agrega_acoes_uso_e_erros() -> None:
    _sessao_tech(
        FakeResult(itens=[(120, 7, 3, 9)]),  # auditoria: ações, usuários, organizações, falhas
        FakeResult(itens=[(5000, 40)]),  # operacional: requisições, erros
        FakeResult(itens=[(2, 5)]),  # organizações ativas: sem uso, total
    )

    resposta = TestClient(app).get("/v1/admin/monitoramento/resumo?dias=30")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["acoes"] == 120
    assert corpo["usuarios_ativos"] == 7
    assert corpo["organizacoes_ativas"] == 3
    assert corpo["organizacoes_sem_uso"] == 2
    assert corpo["total_organizacoes_ativas"] == 5
    assert corpo["acoes_com_falha"] == 9
    assert corpo["taxa_falha_acoes"] == pytest.approx(9 / 120)
    assert corpo["requisicoes"] == 5000
    assert corpo["erros"] == 40
    assert corpo["taxa_erros"] == pytest.approx(40 / 5000)


def test_resumo_sem_dados_nao_divide_por_zero() -> None:
    _sessao_tech(
        FakeResult(itens=[(0, 0, 0, 0)]),
        FakeResult(itens=[(0, 0)]),
        FakeResult(itens=[(0, 0)]),
    )

    resposta = TestClient(app).get("/v1/admin/monitoramento/resumo")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["taxa_erros"] == 0
    assert corpo["taxa_falha_acoes"] == 0


def test_resumo_exige_acesso_tech() -> None:
    _sessao_tech(perfil="operador")

    resposta = TestClient(app).get("/v1/admin/monitoramento/resumo")

    assert resposta.status_code == 403


def test_serie_preenche_dias_sem_evento() -> None:
    hoje = datetime.now(UTC).astimezone(FUSO).date()
    _sessao_tech(
        FakeResult(itens=[(hoje, 11, 2)]),  # auditoria de hoje
        FakeResult(itens=[(hoje, 300, 5)]),  # operacional de hoje
    )

    resposta = TestClient(app).get("/v1/admin/monitoramento/serie?dias=7")

    assert resposta.status_code == 200
    pontos = resposta.json()["pontos"]
    assert len(pontos) == 7
    assert pontos[0]["dia"] == (hoje - timedelta(days=6)).isoformat()
    assert pontos[-1]["dia"] == hoje.isoformat()
    assert pontos[-1]["acoes"] == 11
    assert pontos[-1]["frustradas"] == 2
    assert pontos[-1]["erros"] == 5
    assert all(ponto["acoes"] == 0 for ponto in pontos[:-1])


def test_uso_por_organizacao_lista_metricas_e_sem_uso() -> None:
    agora = datetime.now(UTC)
    _sessao_tech(
        FakeResult(itens=[(1, "Escritório Alfa", 88, 4, 6, agora)]),
        FakeResult(itens=[(2, "Escritório Beta")]),
    )

    resposta = TestClient(app).get("/v1/admin/monitoramento/uso-organizacoes?dias=30")

    assert resposta.status_code == 200
    corpo = resposta.json()
    org = corpo["organizacoes"][0]
    assert org["nome"] == "Escritório Alfa"
    assert org["acoes"] == 88
    assert org["usuarios_ativos"] == 4
    assert org["falhas"] == 6
    assert org["taxa_falha"] == pytest.approx(6 / 88)
    assert corpo["sem_uso"] == [{"id": 2, "nome": "Escritório Beta"}]


def test_top_erros_agrupa_por_codigo_e_operacao() -> None:
    agora = datetime.now(UTC)
    _sessao_tech(
        FakeResult(itens=[("http_500", "POST /v1/admin/carteira", 500, 17, agora, 42.0)]),
    )

    resposta = TestClient(app).get("/v1/admin/monitoramento/top-erros")

    assert resposta.status_code == 200
    erro = resposta.json()["erros"][0]
    assert erro["codigo_erro"] == "http_500"
    assert erro["operacao"] == "POST /v1/admin/carteira"
    assert erro["quantidade"] == 17
    assert erro["duracao_media_ms"] == 42.0


def test_dificuldade_devolve_os_quatro_sinais() -> None:
    agora = datetime.now(UTC)
    _sessao_tech(
        FakeResult(itens=[("POST /v1/admin/leads", 422, 13, agora)]),  # 4xx por operação
        FakeResult(itens=[("ana@alfa.com", "Escritório Alfa", 9, 3, agora)]),  # usuários com falhas
        FakeResult(itens=[("ana@alfa.com", "POST /v1/admin/leads", 5, agora)]),  # recursos travados
        FakeResult(itens=[("api", "GET /v1/admin/processos", 40, 180.0, 900.0, 2400)]),  # lentidão
    )

    resposta = TestClient(app).get("/v1/admin/monitoramento/dificuldade")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["tentativas_frustradas"][0]["status_http"] == 422
    assert corpo["usuarios_com_falhas"][0]["ator"] == "ana@alfa.com"
    assert corpo["usuarios_com_falhas"][0]["recursos_distintos"] == 3
    assert corpo["recursos_travados"][0]["falhas"] == 5
    assert corpo["lentidao"][0]["p95_ms"] == 900.0


def test_dificuldade_exige_acesso_tech() -> None:
    _sessao_tech(perfil="operador")

    resposta = TestClient(app).get("/v1/admin/monitoramento/dificuldade")

    assert resposta.status_code == 403


def test_pagina_monitoramento_restrita_e_servida() -> None:
    client = TestClient(app)
    assert client.get("/admin/monitoramento", follow_redirects=False).status_code == 303
    app.dependency_overrides[obter_usuario_atual] = auth_override()
    response = TestClient(app).get("/admin/monitoramento")
    assert response.status_code == 200
    assert 'data-admin-section="monitoramento"' in response.text
    assert "Monitoramento de erro e uso" in response.text

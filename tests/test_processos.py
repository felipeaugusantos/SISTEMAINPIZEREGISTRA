from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from app.models import Processo, TipoProcesso, Titular
from tests.conftest import FakeResult, sessao_override


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


def test_nome_muito_curto_retorna_422() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    assert TestClient(app).get("/v1/processos", params={"nome": "a"}).status_code == 422


def test_busca_retorna_resultados() -> None:
    processo = Processo(
        numero="943906024",
        tipo=TipoProcesso.MARCA,
        titulo="ACME",
        data_deposito=None,
        situacao="Deferido",
        fonte="RPI 2897",
    )
    processo.atualizado_em = datetime.now(UTC)
    processo.titulares = [Titular(nome="EMPRESA TESTE", pais="BR")]

    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=1),
        FakeResult(itens=[processo]),
    )
    resposta = TestClient(app).get("/v1/processos", params={"nome": "acme"})

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["itens"][0]["numero"] == "943906024"
    assert corpo["itens"][0]["titulares"][0]["nome"] == "EMPRESA TESTE"

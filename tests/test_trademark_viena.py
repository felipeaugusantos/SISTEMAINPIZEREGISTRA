from datetime import date
from types import SimpleNamespace

import pytest

from app.trademarks.viena import avaliar_afinidade_viena, buscar_anterioridades_viena
from tests.conftest import FakeResult, FakeSession


def test_viena_codigos_identicos_sao_marcados_como_identica() -> None:
    resultado = avaliar_afinidade_viena(["26.4.1"], ["26.4.1", "05.1.1"], [])
    assert resultado.nivel == "identica"
    assert "26.4.1" in resultado.codigos_comuns


def test_viena_mesma_divisao_e_alta() -> None:
    resultado = avaliar_afinidade_viena(["26.4.1"], ["26.4.5"], [])
    assert resultado.nivel == "alta"
    assert resultado.codigos_comuns == ("26.4",)


def test_viena_mesma_categoria_e_moderada() -> None:
    resultado = avaliar_afinidade_viena(["26.4.1"], ["26.13.25"], [])
    assert resultado.nivel == "moderada"


def test_viena_sem_relacao_e_nao_mapeada() -> None:
    resultado = avaliar_afinidade_viena(["26.4.1"], ["05.1.1"], [])
    assert resultado.nivel == "nao_mapeada"


def test_viena_matriz_curada_pendente_de_revisao() -> None:
    matriz = [
        SimpleNamespace(
            codigo_origem="03.1.1",
            codigo_destino="04.1.1",
            nivel="alta",
            justificativa="Animais estilizados semelhantes",
            status_revisao="pendente",
        )
    ]
    resultado = avaliar_afinidade_viena(["03.1.1"], ["04.1.1"], matriz)
    assert resultado.nivel == "alta"
    assert resultado.revisao == "pendente"


def test_viena_sem_dados_retorna_sem_dados() -> None:
    assert avaliar_afinidade_viena([], ["26.4.1"], []).nivel == "sem_dados"


@pytest.mark.asyncio
async def test_busca_anterioridades_viena_ranqueia_por_sobreposicao() -> None:
    linhas = [
        ("900000002", "Marca B", "mista", date(2020, 1, 1), ["26.4.1", "26.4.5"], 2),
        ("900000001", "Marca A", "figurativa", date(2019, 1, 1), ["26.4.1"], 1),
    ]
    session = FakeSession([FakeResult(itens=linhas)])
    resultado = await buscar_anterioridades_viena(session, ["26.4.1", "26.4.5"], 10)
    assert [item["numero"] for item in resultado] == ["900000002", "900000001"]
    assert resultado[0]["codigos_em_comum"] == 2
    assert resultado[0]["url_detalhe"] == "/processos/900000002"


@pytest.mark.asyncio
async def test_busca_anterioridades_viena_sem_codigos_retorna_vazio() -> None:
    session = FakeSession([])
    assert await buscar_anterioridades_viena(session, ["   ", ""], 10) == []

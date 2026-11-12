"""Testes da cobertura de depósitos em avaliar_qualidade_base.

Caso real que motivou: a base tinha registro com data_deposito em 2030 e a
"Cobertura de depósitos" dos relatórios exibia esse teto futuro como se
fosse legítimo.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

import app.trademarks.quality as quality
from app.trademarks.quality import avaliar_qualidade_base


class _Resultado:
    def __init__(self, valor: Any) -> None:
        self._valor = valor

    def scalar_one_or_none(self) -> Any:
        return self._valor

    def one(self) -> Any:
        return self._valor


class _SessaoFalsa:
    """Devolve, em ordem: última importação, última data de RPI e cobertura."""

    def __init__(self, cobertura: tuple) -> None:
        self._respostas = [
            _Resultado(SimpleNamespace(numero_rpi=2914, importado_em=datetime(2026, 11, 11, tzinfo=UTC))),
            _Resultado(date(2026, 11, 10)),
            _Resultado(cobertura),
        ]

    async def execute(self, _consulta: Any) -> _Resultado:
        return self._respostas.pop(0)


@pytest.fixture(autouse=True)
def _limpar_cache_cobertura():
    quality._COBERTURA_CACHE = None
    yield
    quality._COBERTURA_CACHE = None


async def test_cobertura_ignora_depositos_futuros_e_avisa() -> None:
    hoje = datetime.now(UTC).date()
    cobertura = (
        1_000_000,  # total de processos
        date(1912, 9, 11),  # depósito mais antigo
        date(2030, 7, 18),  # max bruto (erro de origem)
        3,  # depósitos com data futura
        date(2026, 11, 5),  # max considerando só depósitos até hoje
    )

    resultado = await avaliar_qualidade_base(_SessaoFalsa(cobertura), [])

    assert resultado["depositos_futuros"] == 3
    assert resultado["deposito_mais_recente"] == date(2026, 11, 5)
    assert resultado["deposito_mais_recente"] <= hoje
    assert any("depósito futura" in aviso and "18/07/2030" in aviso for aviso in resultado["avisos"])


async def test_cobertura_sem_depositos_futuros_nao_avisa() -> None:
    cobertura = (
        1_000_000,
        date(1912, 9, 11),
        date(2026, 11, 5),
        0,
        date(2026, 11, 5),
    )

    resultado = await avaliar_qualidade_base(_SessaoFalsa(cobertura), [])

    assert resultado["depositos_futuros"] == 0
    assert resultado["deposito_mais_recente"] == date(2026, 11, 5)
    assert not any("depósito futura" in aviso for aviso in resultado["avisos"])

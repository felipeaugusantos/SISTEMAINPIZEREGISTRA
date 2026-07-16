from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any


class FakeResult:
    """Resultado de execute() configurável para os testes de endpoint."""

    def __init__(self, scalar: Any = None, itens: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._itens = itens if itens is not None else []

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return list(self._itens)


class FakeSession:
    """Sessão async que devolve resultados enfileirados, sem tocar no banco."""

    def __init__(self, resultados: list[FakeResult] | None = None) -> None:
        self._resultados = list(resultados or [])
        self.adicionados: list[Any] = []
        self.commits = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> FakeResult:
        if self._resultados:
            return self._resultados.pop(0)
        return FakeResult()

    def add(self, obj: Any) -> None:
        self.adicionados.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj: Any) -> None:
        # Simula o preenchimento de colunas geradas pelo banco após o commit.
        if getattr(obj, "id", None) is None:
            obj.id = 1
        agora = datetime.now(UTC)
        if getattr(obj, "criado_em", None) is None:
            obj.criado_em = agora
        if getattr(obj, "atualizado_em", None) is None:
            obj.atualizado_em = agora

    async def get(self, *_args: Any, **_kwargs: Any) -> Any:
        return None


def sessao_override(*resultados: FakeResult) -> Any:
    async def _override() -> Iterator[FakeSession]:
        yield FakeSession(list(resultados))

    return _override

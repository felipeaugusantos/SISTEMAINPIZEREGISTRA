from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from app.auth import UsuarioAutenticado


def usuario_teste(perfil: str = "administrador", permissoes: set[str] | None = None) -> UsuarioAutenticado:
    return UsuarioAutenticado(
        id=1,
        nome="Admin Teste",
        usuario="admin",
        email="admin@teste.local",
        perfil=perfil,
        permissoes=frozenset(permissoes or set()),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash="",
    )


def auth_override(usuario: UsuarioAutenticado | None = None):
    async def _override() -> UsuarioAutenticado:
        return usuario or usuario_teste()

    return _override


class FakeResult:
    """Resultado de execute() configurável para os testes de endpoint."""

    def __init__(self, scalar: Any = None, itens: list[Any] | None = None, rowcount: int = 0) -> None:
        self._scalar = scalar
        self._itens = itens if itens is not None else []
        self.rowcount = rowcount

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> "FakeResult":
        return self

    def __iter__(self):
        # Espelha ScalarResult real do SQLAlchemy: iterável diretamente, sem
        # precisar chamar .all() -- padrão usado em list((await session.execute(...)).scalars()).
        return iter(self._itens)

    def all(self) -> list[Any]:
        return list(self._itens)

    def first(self) -> Any:
        return self._itens[0] if self._itens else None

    def one(self) -> Any:
        if len(self._itens) != 1:
            raise AssertionError(f"Era esperada uma linha, mas foram recebidas {len(self._itens)}")
        return self._itens[0]


class FakeSession:
    """Sessão async que devolve resultados enfileirados, sem tocar no banco."""

    def __init__(self, resultados: list[FakeResult] | None = None, objetos_get: list[Any] | None = None) -> None:
        self._resultados = list(resultados or [])
        self._objetos_get = list(objetos_get or [])
        self.adicionados: list[Any] = []
        self.executados: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any = None, *_args: Any, **_kwargs: Any) -> FakeResult:
        self.executados.append(statement)
        if self._resultados:
            return self._resultados.pop(0)
        return FakeResult()

    def add(self, obj: Any) -> None:
        self.adicionados.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

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
        if self._objetos_get:
            return self._objetos_get.pop(0)
        return None


def sessao_override(*resultados: FakeResult) -> Any:
    async def _override() -> Iterator[FakeSession]:
        yield FakeSession(list(resultados))

    return _override

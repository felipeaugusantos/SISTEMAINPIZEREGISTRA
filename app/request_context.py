from contextvars import ContextVar, Token
from uuid import uuid4

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_detalhes_operacionais: ContextVar[dict | None] = ContextVar("detalhes_operacionais", default=None)


def novo_request_id() -> str:
    return str(uuid4())


def request_id_atual() -> str | None:
    return _request_id.get()


def definir_request_id(valor: str | None = None) -> Token[str | None]:
    return _request_id.set(valor or novo_request_id())


def restaurar_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def iniciar_detalhes_operacionais() -> Token[dict | None]:
    return _detalhes_operacionais.set({})


def adicionar_detalhes_operacionais(**detalhes) -> None:
    atuais = dict(_detalhes_operacionais.get() or {})
    atuais.update(detalhes)
    _detalhes_operacionais.set(atuais)


def detalhes_operacionais() -> dict:
    return dict(_detalhes_operacionais.get() or {})


def restaurar_detalhes_operacionais(token: Token[dict | None]) -> None:
    _detalhes_operacionais.reset(token)

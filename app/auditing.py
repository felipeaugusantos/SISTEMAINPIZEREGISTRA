from typing import Any

from sqlalchemy import event

from app.models import EventoAuditoria

CHAVES_SENSIVEIS = frozenset(
    {
        "authorization",
        "chave",
        "client_secret",
        "codigo_mfa",
        "codigos_recuperacao",
        "cookie",
        "csrf",
        "mfa_segredo",
        "nova_senha",
        "password",
        "senha",
        "senha_atual",
        "senha_hash",
        "secret",
        "segredo",
        "token",
        "token_hash",
    }
)


def _chave_sensivel(chave: str) -> bool:
    normalizada = chave.strip().lower()
    return any(item in normalizada for item in CHAVES_SENSIVEIS)


def mascarar_dados_auditoria(valor: Any, *, chave: str = "") -> Any:
    if chave and _chave_sensivel(chave):
        return "[REDACTED]"
    if isinstance(valor, dict):
        return {str(item): mascarar_dados_auditoria(conteudo, chave=str(item)) for item, conteudo in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [mascarar_dados_auditoria(item) for item in valor]
    return valor


def criar_evento_auditoria(
    *,
    organizacao_id: int | None,
    ator: str,
    acao: str,
    recurso: str,
    sucesso: bool,
    status_http: int,
    request_id: str | None = None,
    detalhes: dict | None = None,
    actor_id: int | None = None,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    ip_hash: str | None = None,
    before_state: dict | None = None,
    after_state: dict | None = None,
) -> EventoAuditoria:
    return EventoAuditoria(
        organizacao_id=organizacao_id,
        actor_id=actor_id,
        ator=ator[:150],
        acao=acao[:20],
        recurso=recurso[:180],
        resource_type=resource_type[:80] if resource_type else None,
        resource_id=str(resource_id)[:120] if resource_id is not None else None,
        sucesso=sucesso,
        status_http=status_http,
        request_id=request_id,
        ip_hash=ip_hash,
        detalhes=mascarar_dados_auditoria(detalhes or {}),
        before_state=mascarar_dados_auditoria(before_state) if before_state else None,
        after_state=mascarar_dados_auditoria(after_state) if after_state else None,
    )


@event.listens_for(EventoAuditoria, "before_insert")
def _normalizar_evento_antes_de_persistir(_mapper, _connection, evento: EventoAuditoria) -> None:
    evento.detalhes = mascarar_dados_auditoria(evento.detalhes or {})
    evento.before_state = mascarar_dados_auditoria(evento.before_state)
    evento.after_state = mascarar_dados_auditoria(evento.after_state)
    if not evento.resource_type and ":" in evento.recurso:
        resource_type, resource_id = evento.recurso.split(":", 1)
        evento.resource_type = resource_type[:80]
        evento.resource_id = resource_id[:120] or None

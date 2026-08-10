import hashlib
import hmac
import os
import re
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import Request, Response

from app.database import session_factory
from app.models import EventoAuditoria, EventoOperacional
from app.proxy import cliente_ip
from app.settings import get_settings

CallNext = Callable[[Request], Awaitable[Response]]
IDENTIFICADOR_CAMINHO = re.compile(
    r"/(?:[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9]{2,})(?=/|$)",
    re.IGNORECASE,
)


def _ator_sessao(request: Request) -> str:
    usuario = getattr(request.state, "auth_user", None)
    return getattr(usuario, "email", "anonimo")[:150]


def _recurso(request: Request) -> str:
    rota = request.scope.get("route")
    caminho = getattr(rota, "path", None)
    if caminho:
        return str(caminho)[:180]
    return IDENTIFICADOR_CAMINHO.sub("/{id}", request.url.path)[:180]


def _componente(caminho: str) -> str | None:
    if caminho == "/v1/admin/rpi":
        return None
    if caminho.startswith("/v1/pesquisas-marca") and caminho.endswith("/relatorio"):
        return "relatorio"
    if caminho.startswith("/v1/admin"):
        return "administracao"
    if caminho.startswith("/v1/processos"):
        return "consulta"
    return None


def _hash_ip(request: Request) -> str | None:
    segredo = get_settings().audit_ip_salt.encode("utf-8")
    return hmac.new(
        segredo,
        cliente_ip(request).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def _registrar(
    request: Request,
    status_http: int,
    duracao_ms: int,
    codigo_erro: str | None,
) -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    recurso = _recurso(request)
    componente = _componente(request.url.path)
    leitura_monitoramento = request.method == "GET" and request.url.path == "/v1/admin/rpi"
    administrativo = (
        request.url.path.startswith(("/admin", "/v1/admin"))
        and not leitura_monitoramento
    )
    if componente is None and not administrativo:
        return
    try:
        async with session_factory() as session:
            if componente is not None:
                session.add(
                    EventoOperacional(
                        componente=componente,
                        operacao=f"{request.method} {recurso}",
                        sucesso=status_http < 400,
                        duracao_ms=duracao_ms,
                        status_http=status_http,
                        codigo_erro=codigo_erro,
                    )
                )
            if administrativo:
                session.add(
                    EventoAuditoria(
                        organizacao_id=getattr(getattr(request.state, "auth_user", None), "organizacao_id", None),
                        ator=_ator_sessao(request),
                        acao=request.method,
                        recurso=recurso,
                        sucesso=status_http < 400,
                        status_http=status_http,
                        ip_hash=_hash_ip(request),
                        detalhes={"metodo": request.method},
                    )
                )
            await session.commit()
    except Exception:
        # Falhas de observabilidade nunca podem interromper a operação principal.
        return


# Swagger/Redoc dependem de scripts inline e CDN; nao recebem o CSP estrito.
_ROTAS_DOCUMENTACAO = ("/docs", "/redoc", "/openapi.json")
# Rotas sensiveis nao podem ser cacheadas por proxies/navegador.
_ROTAS_SENSIVEIS = ("/admin", "/v1/admin", "/login", "/alterar-senha", "/v1/auth")
_CSP_PADRAO = (
    "default-src 'self'; img-src 'self' data: https:; "
    "style-src 'self'; script-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _cabecalhos_seguranca(response: Response, request: Request) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    caminho = request.url.path
    if caminho.startswith(_ROTAS_DOCUMENTACAO):
        return
    # CSP e protecao contra clickjacking em todas as paginas da aplicacao (inclui publicas).
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", _CSP_PADRAO)
    if caminho.startswith(_ROTAS_SENSIVEIS):
        response.headers["Cache-Control"] = "no-store"


async def observar_requisicao(request: Request, call_next: CallNext) -> Response:
    inicio = perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        duracao = max(0, round((perf_counter() - inicio) * 1000))
        await _registrar(request, 500, duracao, type(exc).__name__)
        raise
    duracao = max(0, round((perf_counter() - inicio) * 1000))
    _cabecalhos_seguranca(response, request)
    codigo_erro = f"http_{response.status_code}" if response.status_code >= 400 else None
    await _registrar(request, response.status_code, duracao, codigo_erro)
    return response

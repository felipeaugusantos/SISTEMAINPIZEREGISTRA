import hashlib
import hmac
import json
import logging
import os
import re
import traceback
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import Request, Response

from app.auditing import criar_evento_auditoria
from app.database import session_factory
from app.models import EventoOperacional
from app.proxy import cliente_ip
from app.request_context import (
    adicionar_detalhes_operacionais,
    definir_request_id,
    detalhes_operacionais,
    iniciar_detalhes_operacionais,
    request_id_atual,
    restaurar_detalhes_operacionais,
    restaurar_request_id,
)
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

CallNext = Callable[[Request], Awaitable[Response]]
logger = logging.getLogger("ze_registra.http")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False
REQUEST_ID_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
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


def _tipo_e_id_recurso(request: Request) -> tuple[str | None, str | None]:
    partes = [item for item in request.url.path.split("/") if item]
    uteis = [item for item in partes if item not in {"v1", "admin"}]
    tipo = uteis[0] if uteis else None
    identificador = next(
        (item for item in reversed(uteis) if item.isdigit() or len(item) >= 32),
        None,
    )
    return tipo, identificador


def _componente(caminho: str) -> str | None:
    if caminho == "/v1/admin/rpi":
        return None
    if caminho.startswith("/v1/pesquisas-marca") and caminho.endswith("/relatorio"):
        return "relatorio"
    if caminho.startswith("/v1/admin"):
        return "administracao"
    if caminho.startswith("/v1/processos"):
        return "consulta"
    if caminho.startswith("/v1"):
        return "api"
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
    administrativo = request.url.path.startswith(("/admin", "/v1/admin")) and not leitura_monitoramento
    if componente is None and not administrativo:
        return
    try:
        async with session_factory() as session:
            usuario = getattr(request.state, "auth_user", None)
            organizacao_id = getattr(usuario, "organizacao_id", None)
            if organizacao_id is not None:
                await aplicar_contexto_tenant(
                    session,
                    organizacao_id,
                    superadmin=getattr(usuario, "superadmin", False),
                )
            if componente is not None:
                session.add(
                    EventoOperacional(
                        componente=componente,
                        operacao=f"{request.method} {recurso}",
                        request_id=request.state.request_id,
                        sucesso=status_http < 400,
                        duracao_ms=duracao_ms,
                        status_http=status_http,
                        codigo_erro=codigo_erro,
                        detalhes=detalhes_operacionais(),
                    )
                )
            if administrativo and organizacao_id is not None:
                resource_type, resource_id = _tipo_e_id_recurso(request)
                session.add(
                    criar_evento_auditoria(
                        organizacao_id=organizacao_id,
                        actor_id=getattr(usuario, "id", None),
                        ator=_ator_sessao(request),
                        acao=request.method,
                        recurso=recurso,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        request_id=request.state.request_id,
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
# CAPTCHA da consulta pública (Cloudflare Turnstile): o widget é um script
# e um iframe servidos por challenges.cloudflare.com -- só essa origem é
# liberada, e só para script e frame.
_ORIGEM_TURNSTILE = "https://challenges.cloudflare.com"
_CSP_PADRAO = (
    "default-src 'self'; img-src 'self' data: https:; "
    f"style-src 'self'; script-src 'self' {_ORIGEM_TURNSTILE}; connect-src 'self'; "
    f"frame-src {_ORIGEM_TURNSTILE}; "
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
    recebido = request.headers.get("X-Request-ID", "").strip()
    request_id = recebido if REQUEST_ID_VALIDO.fullmatch(recebido) else None
    token_contexto = definir_request_id(request_id)
    token_detalhes = iniciar_detalhes_operacionais()
    request.state.request_id = request_id_valor = request_id_atual()
    inicio = perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            duracao = max(0, round((perf_counter() - inicio) * 1000))
            adicionar_detalhes_operacionais(
                erro_mensagem=str(exc)[:500],
                erro_traceback=traceback.format_exc()[:6000],
            )
            await _registrar(request, 500, duracao, type(exc).__name__)
            logger.exception(
                json.dumps(
                    {
                        "event": "HTTP_REQUEST_FAILED",
                        "request_id": request_id_valor,
                        "method": request.method,
                        "path": _recurso(request),
                        "status_http": 500,
                        "duration_ms": duracao,
                        "error": type(exc).__name__,
                    },
                    ensure_ascii=False,
                )
            )
            raise
        duracao = max(0, round((perf_counter() - inicio) * 1000))
        _cabecalhos_seguranca(response, request)
        response.headers["X-Request-ID"] = request_id_valor or ""
        codigo_erro = f"http_{response.status_code}" if response.status_code >= 400 else None
        await _registrar(request, response.status_code, duracao, codigo_erro)
        logger.info(
            json.dumps(
                {
                    "event": "HTTP_REQUEST_COMPLETED",
                    "request_id": request_id_valor,
                    "organization_id": getattr(getattr(request.state, "auth_user", None), "organizacao_id", None),
                    "user_id": getattr(getattr(request.state, "auth_user", None), "id", None),
                    "method": request.method,
                    "path": _recurso(request),
                    "status_http": response.status_code,
                    "duration_ms": duracao,
                },
                ensure_ascii=False,
            )
        )
        return response
    finally:
        restaurar_detalhes_operacionais(token_detalhes)
        restaurar_request_id(token_contexto)

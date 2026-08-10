import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet, import_key
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import (
    UsuarioAtualDep,
    criar_sessao,
    definir_cookies_sessao,
    exigir_csrf,
    hash_ip,
    hash_token,
)
from app.database import get_session
from app.models import (
    EventoAuditoria,
    IdentidadeExterna,
    TentativaOAuth,
    UsuarioOperacoes,
)
from app.proxy import cliente_ip
from app.ratelimit import RateLimiter
from app.security_ext import revelar_segredo, validar_totp
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

router = APIRouter(prefix="/v1/auth/social", tags=["autenticacao-social"])
limitar_oauth = RateLimiter(limite=20, janela_segundos=300, escopo="oauth")
MFA_COOKIE = "zr_oauth_mfa"
OAUTH_BROWSER_COOKIE = "zr_oauth_browser"

PROVEDORES = {
    "google": {
        "nome": "Google",
        "issuer": "https://accounts.google.com",
        "discovery": "https://accounts.google.com/.well-known/openid-configuration",
        "scope": "openid email profile",
    },
    "apple": {
        "nome": "Apple",
        "issuer": "https://appleid.apple.com",
        "authorization_endpoint": "https://appleid.apple.com/auth/authorize",
        "token_endpoint": "https://appleid.apple.com/auth/token",
        "jwks_uri": "https://appleid.apple.com/auth/keys",
        "scope": "name email",
    },
}


class CodigoMfaSocialInput(BaseModel):
    codigo: str = Field(min_length=6, max_length=20)


def _configuracao(provedor: str) -> dict:
    settings = get_settings()
    if provedor == "google":
        return {
            **PROVEDORES[provedor],
            "enabled": settings.google_oauth_enabled,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
        }
    if provedor == "apple":
        return {
            **PROVEDORES[provedor],
            "enabled": settings.apple_oauth_enabled,
            "client_id": settings.apple_client_id,
        }
    raise HTTPException(status_code=404, detail="Provedor de acesso desconhecido")


def _redirect_uri(provedor: str) -> str:
    return f"{get_settings().app_public_url.rstrip('/')}/v1/auth/social/{provedor}/callback"


def _destino_seguro(valor: str | None) -> str:
    if valor and valor.startswith("/") and not valor.startswith("//"):
        return valor[:300]
    return "/admin"


def _pkce(verifier: str) -> str:
    resumo = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(resumo).rstrip(b"=").decode()


def _verdadeiro(valor: object) -> bool:
    return valor is True or (isinstance(valor, str) and valor.lower() == "true")


def _validar_claims(claims: dict, config: dict, nonce: str) -> None:
    agora = int(datetime.now(UTC).timestamp())
    audiencia = claims.get("aud", [])
    if isinstance(audiencia, str):
        audiencia = [audiencia]
    valido = (
        claims.get("iss") == config["issuer"]
        and config["client_id"] in audiencia
        and int(claims.get("exp", 0)) >= agora - 60
        and bool(claims.get("sub"))
        and secrets.compare_digest(str(claims.get("nonce", "")), nonce)
    )
    if not valido:
        raise ValueError("Claims do token de identidade invalidas")


async def _metadados(provedor: str, config: dict) -> dict:
    if provedor == "apple":
        return config
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        resposta = await client.get(config["discovery"])
        resposta.raise_for_status()
        return {**config, **resposta.json()}


def _client_secret_apple() -> str:
    settings = get_settings()
    agora = int(datetime.now(UTC).timestamp())
    chave = settings.apple_private_key.replace("\\n", "\n")
    return jwt.encode(
        {"alg": "ES256", "kid": settings.apple_key_id},
        {
            "iss": settings.apple_team_id,
            "iat": agora,
            "exp": agora + 300,
            "aud": "https://appleid.apple.com",
            "sub": settings.apple_client_id,
        },
        import_key(chave),
        algorithms=["ES256"],
    )


async def _criar_tentativa(
    session: AsyncSession,
    provedor: str,
    *,
    modo: str,
    usuario_id: int | None,
    destino: str,
) -> tuple[str, str, TentativaOAuth]:
    settings = get_settings()
    agora = datetime.now(UTC)
    await session.execute(delete(TentativaOAuth).where(TentativaOAuth.expira_em < agora))
    state = secrets.token_urlsafe(40)
    browser_token = secrets.token_urlsafe(40)
    tentativa = TentativaOAuth(
        provedor=provedor,
        state_hash=hash_token(state),
        browser_token_hash=hash_token(browser_token),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64),
        modo=modo,
        usuario_id=usuario_id,
        destino=destino,
        expira_em=agora + timedelta(minutes=settings.oauth_attempt_minutes),
    )
    session.add(tentativa)
    await session.commit()
    return state, browser_token, tentativa


async def _iniciar(
    provedor: str,
    session: AsyncSession,
    *,
    modo: str,
    usuario_id: int | None,
    destino: str,
) -> RedirectResponse:
    config = _configuracao(provedor)
    if not config["enabled"]:
        raise HTTPException(status_code=404, detail="Provedor de acesso desativado")
    state, browser_token, tentativa = await _criar_tentativa(
        session, provedor, modo=modo, usuario_id=usuario_id, destino=destino
    )
    try:
        metadata = await _metadados(provedor, config)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Provedor de acesso indisponivel") from exc
    parametros = {
        "client_id": config["client_id"],
        "redirect_uri": _redirect_uri(provedor),
        "response_type": "code",
        "scope": config["scope"],
        "state": state,
        "nonce": tentativa.nonce,
    }
    if provedor == "google":
        parametros["prompt"] = "select_account"
        parametros["code_challenge"] = _pkce(tentativa.code_verifier)
        parametros["code_challenge_method"] = "S256"
    else:
        parametros["response_mode"] = "form_post"
    resposta = RedirectResponse(
        f"{metadata['authorization_endpoint']}?{urlencode(parametros)}", 302
    )
    settings = get_settings()
    secure = (
        provedor == "apple"
        or settings.app_env.lower() == "production"
        or settings.admin_force_https
    )
    resposta.set_cookie(
        OAUTH_BROWSER_COOKIE,
        browser_token,
        httponly=True,
        secure=secure,
        samesite="none" if provedor == "apple" else "lax",
        max_age=settings.oauth_attempt_minutes * 60,
        path="/v1/auth/social",
    )
    return resposta


@router.get("/providers")
async def provedores() -> dict:
    return {
        "providers": [
            {"id": chave, "nome": dados["nome"], "enabled": _configuracao(chave)["enabled"]}
            for chave, dados in PROVEDORES.items()
        ]
    }


@router.get("/{provedor}/start")
async def iniciar_login(
    provedor: str,
    request: Request,
    next: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    limitar_oauth.aplicar(cliente_ip(request))
    return await _iniciar(
        provedor, session, modo="login", usuario_id=None, destino=_destino_seguro(next)
    )


@router.get("/{provedor}/link")
async def iniciar_vinculo(
    provedor: str,
    request: Request,
    usuario: UsuarioAtualDep,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    limitar_oauth.aplicar(f"usuario:{usuario.id}")
    return await _iniciar(
        provedor,
        session,
        modo="link",
        usuario_id=usuario.id,
        destino="/admin/confiabilidade",
    )


async def _dados_callback(request: Request) -> dict[str, str]:
    if request.method == "POST":
        corpo = (await request.body()).decode("utf-8", errors="replace")
        return {chave: valores[0] for chave, valores in parse_qs(corpo).items() if valores}
    return dict(request.query_params)


async def _trocar_codigo(provedor: str, code: str, tentativa: TentativaOAuth) -> dict:
    config = _configuracao(provedor)
    metadata = await _metadados(provedor, config)
    client_secret = (
        _client_secret_apple() if provedor == "apple" else config["client_secret"]
    )
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        dados_token = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _redirect_uri(provedor),
            "client_id": config["client_id"],
            "client_secret": client_secret,
        }
        if provedor == "google":
            dados_token["code_verifier"] = tentativa.code_verifier
        resposta = await client.post(
            metadata["token_endpoint"],
            data=dados_token,
            headers={"Accept": "application/json"},
        )
        resposta.raise_for_status()
        tokens = resposta.json()
        id_token = tokens.get("id_token")
        if not id_token:
            raise ValueError("Token de identidade ausente")
        chaves = (await client.get(metadata["jwks_uri"])).json()
    token = jwt.decode(
        id_token,
        KeySet.import_key_set(chaves),
        algorithms=["RS256"],
    )
    claims = dict(token.claims)
    _validar_claims(claims, config, tentativa.nonce)
    return claims


def _erro_callback(mensagem: str, destino: str = "/login") -> RedirectResponse:
    separador = "&" if "?" in destino else "?"
    return RedirectResponse(f"{destino}{separador}{urlencode({'oauth_error': mensagem})}", 303)


async def _auditar(
    session: AsyncSession,
    request: Request,
    usuario: UsuarioOperacoes,
    acao: str,
    provedor: str,
) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            ator=usuario.email,
            acao=acao,
            recurso="autenticacao-social",
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes={"provedor": provedor, "usuario_id": usuario.id},
        )
    )


async def _usuario_completo(session: AsyncSession, usuario_id: int) -> UsuarioOperacoes | None:
    return (
        await session.execute(
            select(UsuarioOperacoes)
            .options(selectinload(UsuarioOperacoes.permissoes))
            .where(UsuarioOperacoes.id == usuario_id)
        )
    ).scalar_one_or_none()


async def _resolver_usuario(
    session: AsyncSession, provedor: str, claims: dict
) -> tuple[UsuarioOperacoes | None, IdentidadeExterna | None]:
    identidade = (
        await session.execute(
            select(IdentidadeExterna).where(
                IdentidadeExterna.provedor == provedor,
                IdentidadeExterna.provedor_usuario_id == str(claims["sub"]),
            )
        )
    ).scalar_one_or_none()
    if identidade:
        return await _usuario_completo(session, identidade.usuario_id), identidade
    email = str(claims.get("email") or "").strip().lower()
    if not (
        get_settings().oauth_auto_link_verified_email
        and email
        and _verdadeiro(claims.get("email_verified"))
    ):
        return None, None
    usuario = (
        await session.execute(
            select(UsuarioOperacoes)
            .options(selectinload(UsuarioOperacoes.permissoes))
            .where(UsuarioOperacoes.email == email)
        )
    ).scalar_one_or_none()
    return usuario, None


async def _concluir_sessao(
    session: AsyncSession,
    request: Request,
    response: Response,
    usuario: UsuarioOperacoes,
    provedor: str,
    destino: str,
) -> None:
    usuario.ultimo_login_em = datetime.now(UTC)
    sessao, token, csrf = criar_sessao(usuario.id, request)
    session.add(sessao)
    await _auditar(session, request, usuario, "OAUTH_LOGIN", provedor)
    await session.commit()
    definir_cookies_sessao(response, token, csrf, request)
    response.headers["Location"] = destino


@router.api_route("/{provedor}/callback", methods=["GET", "POST"])
async def callback(
    provedor: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    try:
        config = _configuracao(provedor)
    except HTTPException:
        return _erro_callback("Provedor de acesso desconhecido")
    if not config["enabled"]:
        return _erro_callback("Provedor de acesso desativado")
    dados = await _dados_callback(request)
    state, code = dados.get("state", ""), dados.get("code", "")
    if dados.get("error") or not state or not code:
        return _erro_callback("A autorizacao foi cancelada ou recusada")
    tentativa = (
        await session.execute(
            select(TentativaOAuth).where(
                TentativaOAuth.provedor == provedor,
                TentativaOAuth.state_hash == hash_token(state),
            )
        )
    ).scalar_one_or_none()
    agora = datetime.now(UTC)
    browser_token = request.cookies.get(OAUTH_BROWSER_COOKIE, "")
    browser_valido = bool(
        tentativa
        and browser_token
        and secrets.compare_digest(
            tentativa.browser_token_hash, hash_token(browser_token)
        )
    )
    if (
        not tentativa
        or not browser_valido
        or tentativa.usado_em
        or tentativa.expira_em <= agora
    ):
        return _erro_callback("Tentativa de acesso expirada ou ja utilizada")
    tentativa.usado_em = agora
    try:
        claims = await _trocar_codigo(provedor, code, tentativa)
    except (httpx.HTTPError, JoseError, KeyError, ValueError):
        await session.commit()
        destino_erro = tentativa.destino if tentativa.modo == "link" else "/login"
        return _erro_callback(
            "Nao foi possivel validar a identidade com o provedor", destino_erro
        )

    if tentativa.modo == "link":
        usuario = await _usuario_completo(session, tentativa.usuario_id or 0)
        if not usuario:
            return _erro_callback("A sessao usada para vincular a conta nao existe")
        await aplicar_contexto_tenant(
            session, usuario.organizacao_id, superadmin=usuario.superadmin
        )
        conflito = (
            await session.execute(
                select(IdentidadeExterna).where(
                    IdentidadeExterna.provedor == provedor,
                    or_(
                        IdentidadeExterna.usuario_id == usuario.id,
                        IdentidadeExterna.provedor_usuario_id == str(claims["sub"]),
                    ),
                )
            )
        ).scalar_one_or_none()
        if conflito and conflito.usuario_id != usuario.id:
            await session.commit()
            return _erro_callback("Esta conta ja esta vinculada a outro usuario", tentativa.destino)
        if not conflito:
            session.add(
                IdentidadeExterna(
                    organizacao_id=usuario.organizacao_id,
                    usuario_id=usuario.id,
                    provedor=provedor,
                    provedor_usuario_id=str(claims["sub"]),
                    email_recebido=str(claims.get("email") or "") or None,
                )
            )
        await _auditar(session, request, usuario, "OAUTH_LINK", provedor)
        await session.commit()
        resposta = RedirectResponse(f"{tentativa.destino}?oauth_linked={provedor}", 303)
        resposta.delete_cookie(OAUTH_BROWSER_COOKIE, path="/v1/auth/social")
        return resposta

    usuario, identidade = await _resolver_usuario(session, provedor, claims)
    if not usuario:
        await session.commit()
        return _erro_callback(
            "Conta nao cadastrada. Solicite um convite ou vincule o provedor usando sua senha."
        )
    if not usuario.ativo or (usuario.bloqueado_ate and usuario.bloqueado_ate > agora):
        await session.commit()
        return _erro_callback("Usuario bloqueado ou inativo")
    if usuario.organizacao.status not in {"ativa", "trial"}:
        await session.commit()
        return _erro_callback("Organizacao suspensa")
    await aplicar_contexto_tenant(session, usuario.organizacao_id, superadmin=usuario.superadmin)
    if identidade is None:
        identidade = IdentidadeExterna(
            organizacao_id=usuario.organizacao_id,
            usuario_id=usuario.id,
            provedor=provedor,
            provedor_usuario_id=str(claims["sub"]),
            email_recebido=str(claims.get("email") or "") or None,
        )
        session.add(identidade)
    identidade.ultimo_login_em = agora
    if usuario.mfa_ativo:
        desafio = secrets.token_urlsafe(48)
        tentativa.usuario_id = usuario.id
        tentativa.mfa_token_hash = hash_token(desafio)
        tentativa.expira_em = agora + timedelta(minutes=5)
        await session.commit()
        resposta = RedirectResponse("/login?oauth_mfa=1", 303)
        secure = get_settings().app_env.lower() == "production" or get_settings().admin_force_https
        resposta.set_cookie(
            MFA_COOKIE,
            desafio,
            httponly=True,
            secure=secure,
            samesite="lax",
            max_age=300,
            path="/v1/auth/social",
        )
        resposta.delete_cookie(OAUTH_BROWSER_COOKIE, path="/v1/auth/social")
        return resposta
    resposta = RedirectResponse(tentativa.destino, 303)
    await _concluir_sessao(
        session, request, resposta, usuario, provedor, tentativa.destino
    )
    resposta.delete_cookie(OAUTH_BROWSER_COOKIE, path="/v1/auth/social")
    return resposta


@router.post("/mfa")
async def concluir_mfa(
    dados: CodigoMfaSocialInput,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    token = request.cookies.get(MFA_COOKIE, "")
    tentativa = (
        await session.execute(
            select(TentativaOAuth).where(
                TentativaOAuth.mfa_token_hash == hash_token(token),
                TentativaOAuth.expira_em > datetime.now(UTC),
            )
        )
    ).scalar_one_or_none()
    if not token or not tentativa or not tentativa.usuario_id:
        raise HTTPException(status_code=401, detail="Desafio MFA expirado")
    usuario = await _usuario_completo(session, tentativa.usuario_id)
    if not usuario or not usuario.ativo:
        raise HTTPException(status_code=401, detail="Usuario indisponivel")
    codigo = dados.codigo.strip().upper()
    valido = validar_totp(revelar_segredo(usuario.mfa_segredo or ""), codigo)
    hash_codigo = hash_token(codigo)
    if not valido and hash_codigo in (usuario.codigos_recuperacao or []):
        usuario.codigos_recuperacao = [
            item for item in usuario.codigos_recuperacao if item != hash_codigo
        ]
        valido = True
    if not valido:
        raise HTTPException(status_code=401, detail="Codigo MFA invalido")
    await aplicar_contexto_tenant(session, usuario.organizacao_id, superadmin=usuario.superadmin)
    tentativa.mfa_token_hash = None
    await _concluir_sessao(
        session, request, response, usuario, tentativa.provedor, tentativa.destino
    )
    response.delete_cookie(MFA_COOKIE, path="/v1/auth/social")
    return {"status": "ok", "destino": tentativa.destino}


@router.get("/identities/me")
async def minhas_identidades(
    usuario: UsuarioAtualDep,
    session: AsyncSession = Depends(get_session),
) -> dict:
    identidades = list(
        (
            await session.execute(
                select(IdentidadeExterna).where(IdentidadeExterna.usuario_id == usuario.id)
            )
        ).scalars()
    )
    return {
        "providers": [
            {
                "id": chave,
                "nome": dados["nome"],
                "enabled": _configuracao(chave)["enabled"],
                "linked": any(item.provedor == chave for item in identidades),
                "email": next(
                    (item.email_recebido for item in identidades if item.provedor == chave), None
                ),
            }
            for chave, dados in PROVEDORES.items()
        ]
    }


@router.delete("/identities/{provedor}")
async def desvincular(
    provedor: str,
    request: Request,
    usuario: UsuarioAtualDep,
    session: AsyncSession = Depends(get_session),
) -> dict:
    exigir_csrf(request, usuario)
    if provedor not in PROVEDORES:
        raise HTTPException(status_code=404, detail="Provedor desconhecido")
    identidade = (
        await session.execute(
            select(IdentidadeExterna).where(
                IdentidadeExterna.usuario_id == usuario.id,
                IdentidadeExterna.provedor == provedor,
            )
        )
    ).scalar_one_or_none()
    if not identidade:
        raise HTTPException(status_code=404, detail="Conta nao vinculada")
    await session.delete(identidade)
    registro = await _usuario_completo(session, usuario.id)
    if registro:
        await _auditar(session, request, registro, "OAUTH_UNLINK", provedor)
    await session.commit()
    return {"status": "desvinculada", "provedor": provedor}

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models import SessaoOperacoes, UsuarioOperacoes
from app.permissions import CHAVES_PERMISSAO
from app.ratelimit import RateLimiter
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

SESSION_COOKIE = "zr_session"
CSRF_COOKIE = "zr_csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_limitar_acoes = RateLimiter(limite=60, janela_segundos=60, escopo="sessao-admin")
MODULO_POR_PERMISSAO = {
    "leads": "leads", "validation": "validacao", "risk": "risco", "ai": "ia",
    "learning": "aprendizado", "users": "usuarios", "rpi": "rpi",
    "production": "producao", "audit": "producao",
}


@dataclass(frozen=True)
class UsuarioAutenticado:
    id: int
    nome: str
    usuario: str
    email: str
    perfil: str
    permissoes: frozenset[str]
    alterar_senha: bool
    sessao_id: int
    csrf_hash: str
    organizacao_id: int = 1
    organizacao_slug: str = "ze-registra"
    superadmin: bool = False
    modulos_plano: frozenset[str] = frozenset({
        "consulta", "leads", "validacao", "risco", "ia", "aprendizado",
        "usuarios", "rpi", "producao",
    })

    @property
    def ator(self) -> str:
        return self.email

    def pode(self, chave: str) -> bool:
        return self.superadmin or self.perfil == "administrador" or chave in self.permissoes


def hash_senha(senha: str) -> str:
    return _password_hasher.hash(senha)


def verificar_senha(hash_atual: str, senha: str) -> bool:
    try:
        return _password_hasher.verify(hash_atual, senha)
    except (VerifyMismatchError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    salt = get_settings().audit_ip_salt.encode()
    return hmac.new(salt, ip.encode(), hashlib.sha256).hexdigest()


def gerar_credenciais_sessao() -> tuple[str, str]:
    return secrets.token_urlsafe(48), secrets.token_urlsafe(32)


def _erro_nao_autenticado(request: Request) -> HTTPException:
    if request.url.path.startswith("/admin"):
        destino = quote(request.url.path, safe="/")
        return HTTPException(status_code=303, headers={"Location": f"/login?next={destino}"})
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Autenticacao necessaria")


async def obter_usuario_atual(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsuarioAutenticado:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _erro_nao_autenticado(request)
    stmt = (
        select(SessaoOperacoes)
        .options(selectinload(SessaoOperacoes.usuario).selectinload(UsuarioOperacoes.permissoes))
        .where(SessaoOperacoes.token_hash == hash_token(token))
    )
    sessao = (await session.execute(stmt)).scalar_one_or_none()
    agora = datetime.now(UTC)
    if sessao is None or sessao.revogada_em is not None or sessao.expira_em <= agora:
        raise _erro_nao_autenticado(request)
    limite_ocioso = agora - timedelta(minutes=get_settings().session_idle_minutes)
    if sessao.ultimo_acesso_em < limite_ocioso:
        sessao.revogada_em = agora
        sessao.motivo_revogacao = "inatividade"
        await session.commit()
        raise _erro_nao_autenticado(request)
    usuario = sessao.usuario
    if not usuario.ativo or (usuario.bloqueado_ate and usuario.bloqueado_ate > agora):
        raise HTTPException(status_code=403, detail="Usuario bloqueado")
    if usuario.organizacao.status not in {"ativa", "trial"}:
        raise HTTPException(status_code=403, detail="Organizacao suspensa")
    auth = UsuarioAutenticado(
        id=usuario.id, nome=usuario.nome, usuario=usuario.usuario, email=usuario.email,
        perfil=usuario.perfil, permissoes=frozenset(p.chave for p in usuario.permissoes),
        alterar_senha=usuario.alterar_senha, sessao_id=sessao.id, csrf_hash=sessao.csrf_hash,
        organizacao_id=usuario.organizacao_id, organizacao_slug=usuario.organizacao.slug,
        superadmin=usuario.superadmin,
        modulos_plano=frozenset(usuario.organizacao.plano.modulos or []),
    )
    await aplicar_contexto_tenant(
        session, auth.organizacao_id, superadmin=auth.superadmin
    )
    request.state.auth_user = auth
    if (agora - sessao.ultimo_acesso_em).total_seconds() > 60:
        sessao.ultimo_acesso_em = agora
        await session.commit()
    liberados = {"/v1/auth/me", "/v1/auth/csrf", "/v1/auth/logout", "/v1/auth/trocar-senha", "/alterar-senha"}
    if auth.alterar_senha and request.url.path not in liberados:
        if request.url.path.startswith("/admin"):
            raise HTTPException(status_code=303, headers={"Location": "/alterar-senha"})
        raise HTTPException(status_code=403, detail="Troca de senha obrigatoria")
    return auth


UsuarioAtualDep = Annotated[UsuarioAutenticado, Depends(obter_usuario_atual)]


def limitar_acao_admin(usuario: UsuarioAtualDep) -> None:
    _limitar_acoes.aplicar(f"usuario:{usuario.id}")


AcaoAdminDep = Annotated[None, Depends(limitar_acao_admin)]


def exigir_csrf(request: Request, usuario: UsuarioAutenticado) -> None:
    if request.method in SAFE_METHODS:
        return
    token = request.headers.get("X-CSRF-Token", "")
    if not token or not secrets.compare_digest(hash_token(token), usuario.csrf_hash):
        raise HTTPException(status_code=403, detail="Token CSRF invalido")


def exigir_permissao(chave: str) -> Callable:
    if chave not in CHAVES_PERMISSAO:
        raise ValueError(f"Permissao desconhecida: {chave}")

    async def dependencia(request: Request, usuario: UsuarioAtualDep) -> UsuarioAutenticado:
        exigir_csrf(request, usuario)
        if not usuario.pode(chave):
            raise HTTPException(status_code=403, detail="Acesso nao autorizado")
        modulo = MODULO_POR_PERMISSAO.get(chave.split(".", 1)[0])
        if modulo and not usuario.superadmin and modulo not in usuario.modulos_plano:
            raise HTTPException(status_code=403, detail="Modulo indisponivel no plano contratado")
        if request.method not in SAFE_METHODS:
            _limitar_acoes.aplicar(f"usuario:{usuario.id}")
        return usuario

    return dependencia


def criar_sessao(usuario_id: int, request: Request) -> tuple[SessaoOperacoes, str, str]:
    token, csrf = gerar_credenciais_sessao()
    settings = get_settings()
    sessao = SessaoOperacoes(
        usuario_id=usuario_id, token_hash=hash_token(token), csrf_hash=hash_token(csrf),
        user_agent=request.headers.get("user-agent", "")[:500] or None,
        ip_hash=hash_ip(request.client.host if request.client else None),
        expira_em=datetime.now(UTC) + timedelta(hours=settings.session_duration_hours),
    )
    return sessao, token, csrf

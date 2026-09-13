import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import aplicar_contexto_autenticacao, get_session
from app.models import SessaoOperacoes, UsuarioOperacoes
from app.permissions import CHAVES_PERMISSAO, PERFIS_MFA_OBRIGATORIO
from app.proxy import cliente_ip, requisicao_https
from app.ratelimit import RateLimiter
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

SESSION_COOKIE = "zr_session"
CSRF_COOKIE = "zr_csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_limitar_acoes = RateLimiter(limite=60, janela_segundos=60, escopo="sessao-admin")
MODULO_POR_PERMISSAO = {
    "leads": "leads",
    "validation": "validacao",
    "risk": "risco",
    "ai": "ia",
    "learning": "aprendizado",
    "users": "usuarios",
    "rpi": "rpi",
    "production": "producao",
    "audit": "producao",
    "crm": "crm",
    "prospeccao": "prospeccao",
    "portfolio": "processos_monitorados",
    "legal": "operacao_juridica",
    "finance": "financeiro",
}
MODULO_ALIASES = {"portfolio": "processos_monitorados", "legal": "operacao_juridica"}


def normalizar_modulos_plano(modulos: list[str] | tuple[str, ...] | None) -> frozenset[str]:
    """Normaliza aliases legados sem alterar a configuração persistida do plano."""
    return frozenset(MODULO_ALIASES.get(modulo, modulo) for modulo in (modulos or []))


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
    departamento: str | None = None
    superadmin: bool = False
    mfa_ativo: bool = False
    modulos_plano: frozenset[str] = frozenset(
        {
            "consulta",
            "leads",
            "crm",
            "prospeccao",
            "processos_monitorados",
            "operacao_juridica",
            "validacao",
            "risco",
            "ia",
            "aprendizado",
            "usuarios",
            "rpi",
            "producao",
            "financeiro",
        }
    )

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
        return HTTPException(status_code=303, headers={"Location": "/login"})
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Autenticacao necessaria")


async def obter_usuario_atual(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsuarioAutenticado:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _erro_nao_autenticado(request)
    token_hash = hash_token(token)
    await aplicar_contexto_autenticacao(session, "sessao", token_hash)
    stmt = (
        select(SessaoOperacoes)
        .options(selectinload(SessaoOperacoes.usuario).selectinload(UsuarioOperacoes.permissoes))
        .where(SessaoOperacoes.token_hash == token_hash)
    )
    sessao = (await session.execute(stmt)).scalar_one_or_none()
    agora = datetime.now(UTC)
    if sessao is None or sessao.revogada_em is not None or sessao.expira_em <= agora:
        raise _erro_nao_autenticado(request)
    usuario = sessao.usuario
    # RLS: aplica o contexto de tenant ANTES de qualquer escrita (revogacao por
    # inatividade e atualizacao de ultimo_acesso). Sem isso a policy tenant_write
    # de sessoes_operacoes bloqueia o UPDATE -> 0 linhas -> StaleDataError -> 500.
    await aplicar_contexto_tenant(session, usuario.organizacao_id, superadmin=usuario.superadmin)
    limite_ocioso = agora - timedelta(minutes=get_settings().session_idle_minutes)
    if sessao.ultimo_acesso_em < limite_ocioso:
        sessao.revogada_em = agora
        sessao.motivo_revogacao = "inatividade"
        await session.commit()
        raise _erro_nao_autenticado(request)
    if not usuario.ativo or (usuario.bloqueado_ate and usuario.bloqueado_ate > agora):
        raise HTTPException(status_code=403, detail="Usuario bloqueado")
    if usuario.organizacao.status not in {"ativa", "trial"}:
        raise HTTPException(status_code=403, detail="Organizacao suspensa")
    auth = UsuarioAutenticado(
        id=usuario.id,
        nome=usuario.nome,
        usuario=usuario.usuario,
        email=usuario.email,
        departamento=usuario.departamento,
        perfil=usuario.perfil,
        permissoes=frozenset(p.chave for p in usuario.permissoes),
        alterar_senha=usuario.alterar_senha,
        sessao_id=sessao.id,
        csrf_hash=sessao.csrf_hash,
        organizacao_id=usuario.organizacao_id,
        organizacao_slug=usuario.organizacao.slug,
        superadmin=usuario.superadmin,
        mfa_ativo=usuario.mfa_ativo,
        modulos_plano=normalizar_modulos_plano(
            usuario.organizacao.modulos_liberados
            if usuario.organizacao.modulos_liberados is not None
            else usuario.organizacao.plano.modulos
        ),
    )
    # Contexto de tenant ja aplicado acima (antes das escritas); nada a refazer aqui.
    request.state.auth_user = auth
    if (agora - sessao.ultimo_acesso_em).total_seconds() > 60:
        sessao.ultimo_acesso_em = agora
        await session.commit()
    liberados = {
        "/v1/auth/me",
        "/v1/auth/csrf",
        "/v1/auth/logout",
        "/v1/auth/trocar-senha",
        "/alterar-senha",
    }
    if auth.alterar_senha and request.url.path not in liberados:
        if request.url.path.startswith("/admin"):
            raise HTTPException(status_code=303, headers={"Location": "/alterar-senha"})
        raise HTTPException(status_code=403, detail="Troca de senha obrigatoria")
    liberados_mfa = {
        "/v1/auth/me",
        "/v1/auth/csrf",
        "/v1/auth/logout",
        "/v1/auth/mfa/iniciar",
        "/v1/auth/mfa/confirmar",
        "/configurar-mfa",
    }
    mfa_obrigatorio = auth.superadmin or auth.perfil in PERFIS_MFA_OBRIGATORIO
    if mfa_obrigatorio and not auth.mfa_ativo and request.url.path not in liberados_mfa:
        if request.url.path.startswith("/admin"):
            raise HTTPException(status_code=303, headers={"Location": "/configurar-mfa"})
        raise HTTPException(status_code=403, detail="Configuracao de MFA obrigatoria")
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
        usuario_id=usuario_id,
        token_hash=hash_token(token),
        csrf_hash=hash_token(csrf),
        user_agent=request.headers.get("user-agent", "")[:500] or None,
        ip_hash=hash_ip(cliente_ip(request)),
        expira_em=datetime.now(UTC) + timedelta(hours=settings.session_duration_hours),
    )
    return sessao, token, csrf


def definir_cookies_sessao(response: Response, token: str, csrf: str, request: Request | None = None) -> None:
    settings = get_settings()
    secure = requisicao_https(request)
    comum = {
        "secure": secure,
        "samesite": "lax",
        "max_age": settings.session_duration_hours * 3600,
        "path": "/",
    }
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **comum)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **comum)

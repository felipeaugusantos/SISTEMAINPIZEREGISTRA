import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    UsuarioAtualDep,
    criar_sessao,
    exigir_csrf,
    hash_senha,
    hash_token,
    verificar_senha,
)
from app.database import get_session
from app.models import (
    ConviteOrganizacao,
    EventoAuditoria,
    PermissaoOperacoes,
    SessaoOperacoes,
    TokenRecuperacaoSenha,
    UsuarioOperacoes,
)
from app.ratelimit import RateLimiter
from app.security_ext import (
    gerar_segredo_totp,
    proteger_segredo,
    revelar_segredo,
    uri_totp,
    validar_forca_senha,
    validar_totp,
)
from app.settings import TAMANHO_MINIMO_SENHA, get_settings
from app.tenancy import aplicar_contexto_tenant, validar_limite_usuarios

router = APIRouter(prefix="/v1/auth", tags=["autenticacao"])
limitar_login = RateLimiter(limite=10, janela_segundos=60, escopo="login")
limitar_recuperacao = RateLimiter(limite=5, janela_segundos=300, escopo="recuperacao")
limitar_mfa = RateLimiter(limite=10, janela_segundos=300, escopo="mfa")


class LoginInput(BaseModel):
    identificador: str = Field(min_length=2, max_length=254)
    senha: str = Field(min_length=1, max_length=200)
    codigo_mfa: str | None = Field(default=None, min_length=6, max_length=20)


class TrocarSenhaInput(BaseModel):
    senha_atual: str = Field(min_length=1, max_length=200)
    nova_senha: str = Field(min_length=TAMANHO_MINIMO_SENHA, max_length=200)

    _senha_forte = field_validator("nova_senha")(validar_forca_senha)


class RecuperacaoInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class RedefinirSenhaInput(BaseModel):
    token: str = Field(min_length=20, max_length=300)
    nova_senha: str = Field(min_length=TAMANHO_MINIMO_SENHA, max_length=200)

    _senha_forte = field_validator("nova_senha")(validar_forca_senha)


class CodigoMfaInput(BaseModel):
    codigo: str = Field(min_length=6, max_length=20)


class AceitarConviteInput(BaseModel):
    token: str = Field(min_length=20, max_length=300)
    nome: str = Field(min_length=2, max_length=150)
    usuario: str = Field(pattern=r"^[a-zA-Z0-9._-]{2,80}$")
    senha: str = Field(min_length=TAMANHO_MINIMO_SENHA, max_length=200)

    _senha_forte = field_validator("senha")(validar_forca_senha)


def _resposta_usuario(usuario: UsuarioOperacoes) -> dict:
    return {
        "id": usuario.id,
        "nome": usuario.nome,
        "usuario": usuario.usuario,
        "email": usuario.email,
        "perfil": usuario.perfil,
        "permissoes": sorted(p.chave for p in usuario.permissoes),
        "alterar_senha": usuario.alterar_senha,
        "mfa_ativo": usuario.mfa_ativo,
        "superadmin": usuario.superadmin,
        "organizacao": {
            "id": usuario.organizacao.id,
            "nome": usuario.organizacao.nome,
            "slug": usuario.organizacao.slug,
            "plano": usuario.organizacao.plano.nome,
            "modulos": usuario.organizacao.plano.modulos,
        },
    }


async def _auditar(
    session: AsyncSession, request: Request, ator: str, acao: str, sucesso: bool, detalhes: dict
) -> None:
    from app.auth import hash_ip

    session.add(
        EventoAuditoria(
            organizacao_id=getattr(
                getattr(request.state, "auth_user", None), "organizacao_id", None
            ),
            ator=ator[:150],
            acao=acao[:20],
            recurso="autenticacao",
            sucesso=sucesso,
            status_http=200 if sucesso else 401,
            ip_hash=hash_ip(request.client.host if request.client else None),
            detalhes=detalhes,
        )
    )


@router.post("/login")
async def login(
    dados: LoginInput,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    cliente = request.client.host if request.client else "desconhecido"
    limitar_login.aplicar(cliente)
    ident = dados.identificador.strip().lower()
    usuario = (
        await session.execute(
            select(UsuarioOperacoes)
            .options(selectinload(UsuarioOperacoes.permissoes))
            .where(or_(UsuarioOperacoes.usuario == ident, UsuarioOperacoes.email == ident))
        )
    ).scalar_one_or_none()
    agora = datetime.now(UTC)
    valido = (
        usuario is not None and usuario.ativo and verificar_senha(usuario.senha_hash, dados.senha)
    )
    if valido and usuario.mfa_ativo:
        codigo = dados.codigo_mfa or ""
        valido = validar_totp(revelar_segredo(usuario.mfa_segredo or ""), codigo)
        if not valido and hash_token(codigo.upper()) in (usuario.codigos_recuperacao or []):
            usuario.codigos_recuperacao = [
                item for item in usuario.codigos_recuperacao if item != hash_token(codigo.upper())
            ]
            valido = True
    if usuario is not None and usuario.bloqueado_ate and usuario.bloqueado_ate > agora:
        valido = False
    if not valido:
        bloqueou = False
        if usuario is not None:
            usuario.tentativas_falhas += 1
            if usuario.tentativas_falhas >= 5:
                usuario.bloqueado_ate = agora + timedelta(minutes=15)
                usuario.tentativas_falhas = 0
                bloqueou = True
        request.state.auth_user = None
        await aplicar_contexto_tenant(
            session,
            usuario.organizacao_id if usuario else get_settings().default_organization_id,
            superadmin=usuario is None,
        )
        await _auditar(
            session,
            request,
            ident or "anonimo",
            "BLOQUEIO" if bloqueou else "LOGIN_NEGADO",
            False,
            {"motivo": "excesso_tentativas" if bloqueou else "credenciais_invalidas"},
        )
        await session.commit()
        detalhe = (
            "Código MFA inválido ou ausente"
            if usuario and usuario.mfa_ativo
            else "Usuario ou senha invalidos"
        )
        raise HTTPException(status_code=401, detail=detalhe)
    usuario.tentativas_falhas = 0
    usuario.bloqueado_ate = None
    usuario.ultimo_login_em = agora
    request.state.auth_user = type("LoginTenant", (), {"organizacao_id": usuario.organizacao_id})()
    await aplicar_contexto_tenant(session, usuario.organizacao_id, superadmin=usuario.superadmin)
    sessao, token, csrf = criar_sessao(usuario.id, request)
    session.add(sessao)
    await _auditar(session, request, usuario.email, "LOGIN", True, {"usuario_id": usuario.id})
    await session.commit()
    settings = get_settings()
    secure = settings.app_env.lower() == "production" or settings.admin_force_https
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.session_duration_hours * 3600,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        httponly=False,
        secure=secure,
        samesite="lax",
        max_age=settings.session_duration_hours * 3600,
        path="/",
    )
    return {
        "usuario": _resposta_usuario(usuario),
        "destino": "/alterar-senha" if usuario.alterar_senha else "/admin",
    }


@router.get("/me")
async def me(usuario: UsuarioAtualDep, session: AsyncSession = Depends(get_session)) -> dict:
    registro = (
        await session.execute(
            select(UsuarioOperacoes)
            .options(selectinload(UsuarioOperacoes.permissoes))
            .where(UsuarioOperacoes.id == usuario.id)
        )
    ).scalar_one()
    return _resposta_usuario(registro)


@router.get("/csrf")
async def renovar_csrf(
    response: Response, usuario: UsuarioAtualDep, session: AsyncSession = Depends(get_session)
) -> dict:
    sessao = await session.get(SessaoOperacoes, usuario.sessao_id)
    if sessao is None or sessao.revogada_em is not None:
        raise HTTPException(status_code=401, detail="Sessao invalida")
    token = secrets.token_urlsafe(32)
    sessao.csrf_hash = hash_token(token)
    await session.commit()
    settings = get_settings()
    secure = settings.app_env.lower() == "production" or settings.admin_force_https
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=False,
        secure=secure,
        samesite="lax",
        max_age=settings.session_duration_hours * 3600,
        path="/",
    )
    return {"csrf_token": token}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    usuario: UsuarioAtualDep,
    session: AsyncSession = Depends(get_session),
) -> dict:
    exigir_csrf(request, usuario)
    sessao = await session.get(SessaoOperacoes, usuario.sessao_id)
    if sessao:
        sessao.revogada_em = datetime.now(UTC)
        sessao.motivo_revogacao = "logout"
    await _auditar(session, request, usuario.email, "LOGOUT", True, {"usuario_id": usuario.id})
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"status": "ok"}


@router.post("/trocar-senha")
async def trocar_senha(
    dados: TrocarSenhaInput,
    request: Request,
    usuario: UsuarioAtualDep,
    session: AsyncSession = Depends(get_session),
) -> dict:
    exigir_csrf(request, usuario)
    registro = await session.get(UsuarioOperacoes, usuario.id)
    if not registro or not verificar_senha(registro.senha_hash, dados.senha_atual):
        raise HTTPException(status_code=400, detail="Senha atual invalida")
    if dados.nova_senha == dados.senha_atual:
        raise HTTPException(status_code=400, detail="A nova senha deve ser diferente")
    registro.senha_hash = hash_senha(dados.nova_senha)
    registro.alterar_senha = False
    await session.execute(
        update(SessaoOperacoes)
        .where(
            SessaoOperacoes.usuario_id == usuario.id,
            SessaoOperacoes.id != usuario.sessao_id,
            SessaoOperacoes.revogada_em.is_(None),
        )
        .values(revogada_em=datetime.now(UTC), motivo_revogacao="troca_senha")
    )
    await _auditar(session, request, usuario.email, "TROCA_SENHA", True, {"usuario_id": usuario.id})
    await session.commit()
    return {"status": "ok", "destino": "/admin"}


@router.post("/recuperacao/solicitar")
async def solicitar_recuperacao(
    dados: RecuperacaoInput,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    limitar_recuperacao.aplicar(request.client.host if request.client else "desconhecido")
    usuario = (
        await session.execute(
            select(UsuarioOperacoes).where(
                UsuarioOperacoes.email == dados.email.strip().lower(),
                UsuarioOperacoes.ativo.is_(True),
            )
        )
    ).scalar_one_or_none()
    resposta = {"status": "ok", "mensagem": "Se a conta existir, a recuperação foi criada."}
    if usuario:
        await aplicar_contexto_tenant(session, usuario.organizacao_id, superadmin=usuario.superadmin)
        token = secrets.token_urlsafe(48)
        agora = datetime.now(UTC)
        await session.execute(
            update(TokenRecuperacaoSenha)
            .where(
                TokenRecuperacaoSenha.usuario_id == usuario.id,
                TokenRecuperacaoSenha.usado_em.is_(None),
            )
            .values(usado_em=agora)
        )
        session.add(
            TokenRecuperacaoSenha(
                usuario_id=usuario.id,
                token_hash=hash_token(token),
                expira_em=datetime.now(UTC)
                + timedelta(minutes=get_settings().password_reset_minutes),
            )
        )
        await session.commit()
        if get_settings().app_env.lower() != "production":
            resposta["token_teste_local"] = token
    return resposta


@router.post("/recuperacao/redefinir")
async def redefinir_senha(
    dados: RedefinirSenhaInput, session: AsyncSession = Depends(get_session)
) -> dict:
    agora = datetime.now(UTC)
    item = (
        await session.execute(
            select(TokenRecuperacaoSenha).where(
                TokenRecuperacaoSenha.token_hash == hash_token(dados.token),
                TokenRecuperacaoSenha.usado_em.is_(None),
                TokenRecuperacaoSenha.expira_em > agora,
            )
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(400, "Token inválido ou expirado")
    usuario = await session.get(UsuarioOperacoes, item.usuario_id)
    if not usuario:
        raise HTTPException(400, "Token invalido ou expirado")
    await aplicar_contexto_tenant(session, usuario.organizacao_id, superadmin=usuario.superadmin)
    usuario.senha_hash = hash_senha(dados.nova_senha)
    usuario.alterar_senha = False
    item.usado_em = agora
    await session.execute(
        update(SessaoOperacoes)
        .where(SessaoOperacoes.usuario_id == usuario.id, SessaoOperacoes.revogada_em.is_(None))
        .values(revogada_em=agora, motivo_revogacao="recuperacao_senha")
    )
    await session.commit()
    return {"status": "ok"}


@router.post("/mfa/iniciar")
async def iniciar_mfa(
    request: Request, usuario: UsuarioAtualDep, session: AsyncSession = Depends(get_session)
) -> dict:
    limitar_mfa.aplicar(f"usuario:{usuario.id}")
    exigir_csrf(request, usuario)
    registro = await session.get(UsuarioOperacoes, usuario.id)
    segredo = gerar_segredo_totp()
    registro.mfa_segredo = proteger_segredo(segredo)
    registro.mfa_ativo = False
    await session.commit()
    return {
        "segredo": segredo,
        "uri": uri_totp(segredo, registro.email),
        "status": "aguardando_confirmacao",
    }


@router.post("/mfa/confirmar")
async def confirmar_mfa(
    dados: CodigoMfaInput,
    request: Request,
    usuario: UsuarioAtualDep,
    session: AsyncSession = Depends(get_session),
) -> dict:
    limitar_mfa.aplicar(f"usuario:{usuario.id}")
    exigir_csrf(request, usuario)
    registro = await session.get(UsuarioOperacoes, usuario.id)
    if not registro.mfa_segredo or not validar_totp(
        revelar_segredo(registro.mfa_segredo), dados.codigo
    ):
        raise HTTPException(400, "Código MFA inválido")
    codigos = [secrets.token_hex(5).upper() for _ in range(8)]
    registro.codigos_recuperacao = [hash_token(c) for c in codigos]
    registro.mfa_ativo = True
    await session.commit()
    return {"status": "ativo", "codigos_recuperacao": codigos}


@router.delete("/mfa")
async def desativar_mfa(
    dados: CodigoMfaInput,
    request: Request,
    usuario: UsuarioAtualDep,
    session: AsyncSession = Depends(get_session),
) -> dict:
    exigir_csrf(request, usuario)
    registro = await session.get(UsuarioOperacoes, usuario.id)
    if not registro.mfa_ativo or not validar_totp(
        revelar_segredo(registro.mfa_segredo or ""), dados.codigo
    ):
        raise HTTPException(400, "Código MFA inválido")
    registro.mfa_ativo = False
    registro.mfa_segredo = None
    registro.codigos_recuperacao = []
    await session.commit()
    return {"status": "desativado"}


@router.post("/convites/aceitar", status_code=201)
async def aceitar_convite(
    dados: AceitarConviteInput, session: AsyncSession = Depends(get_session)
) -> dict:
    agora = datetime.now(UTC)
    convite = (
        await session.execute(
            select(ConviteOrganizacao).where(
                ConviteOrganizacao.token_hash == hash_token(dados.token),
                ConviteOrganizacao.aceito_em.is_(None),
                ConviteOrganizacao.expira_em > agora,
            )
        )
    ).scalar_one_or_none()
    if not convite:
        raise HTTPException(400, "Convite inválido ou expirado")
    await aplicar_contexto_tenant(session, convite.organizacao_id)
    duplicado = (
        await session.execute(
            select(UsuarioOperacoes.id).where(
                or_(
                    UsuarioOperacoes.usuario == dados.usuario.lower(),
                    UsuarioOperacoes.email == convite.email,
                )
            )
        )
    ).scalar_one_or_none()
    if duplicado:
        raise HTTPException(409, "Usuário ou e-mail já cadastrado")
    await validar_limite_usuarios(session, convite.organizacao_id)
    registro = UsuarioOperacoes(
        organizacao_id=convite.organizacao_id,
        nome=dados.nome.strip(),
        usuario=dados.usuario.lower(),
        email=convite.email,
        perfil=convite.perfil,
        senha_hash=hash_senha(dados.senha),
        alterar_senha=False,
        criado_por="convite",
    )
    if convite.permissoes:
        registro.permissoes = list(
            (
                await session.execute(
                    select(PermissaoOperacoes).where(
                        PermissaoOperacoes.chave.in_(convite.permissoes)
                    )
                )
            ).scalars()
        )
    session.add(registro)
    convite.aceito_em = agora
    await session.commit()
    return {"status": "aceito", "usuario": registro.usuario}

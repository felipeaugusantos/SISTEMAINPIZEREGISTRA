import secrets
import string
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import UsuarioAutenticado, exigir_permissao, hash_senha
from app.database import get_session
from app.models import (
    EventoAuditoria,
    PermissaoOperacoes,
    SessaoOperacoes,
    UsuarioOperacoes,
)
from app.permissions import (
    CHAVES_PERMISSAO,
    PERFIS,
    PERMISSOES,
    PERMISSOES_FINANCEIRO,
    permissoes_do_perfil,
)
from app.tenancy import validar_limite_usuarios

router = APIRouter(prefix="/v1/admin/usuarios", tags=["usuarios"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("users.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("users.manage"))]
ResetDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("users.reset_password"))]
RevokeDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("users.revoke_sessions"))]


class UsuarioInput(BaseModel):
    nome: str = Field(min_length=2, max_length=150)
    usuario: str = Field(pattern=r"^[a-zA-Z0-9._-]{2,80}$")
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    cargo: str | None = Field(default=None, max_length=150)
    perfil: str = "operador"
    permissoes: list[str] = []

    @field_validator("email")
    @classmethod
    def normalizar_email(cls, valor: str) -> str:
        return valor.strip().lower()


class UsuarioUpdate(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=150)
    email: str | None = Field(default=None, min_length=5, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    cargo: str | None = Field(default=None, max_length=150)
    perfil: str | None = None
    ativo: bool | None = None
    permissoes: list[str] | None = None

    @field_validator("email")
    @classmethod
    def normalizar_email(cls, valor: str | None) -> str | None:
        return valor.strip().lower() if valor is not None else None


def _temporaria() -> str:
    alfabeto = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alfabeto) for _ in range(18))


def _validar(perfil: str, permissoes: list[str]) -> set[str]:
    if perfil not in PERFIS:
        raise HTTPException(422, "Perfil invalido")
    desconhecidas = set(permissoes) - CHAVES_PERMISSAO
    if desconhecidas:
        raise HTTPException(422, f"Permissoes invalidas: {', '.join(sorted(desconhecidas))}")
    if perfil == "financeiro":
        extras = set(permissoes) - PERMISSOES_FINANCEIRO
        if extras:
            raise HTTPException(422, "O perfil financeiro aceita somente permissoes financeiras")
        return set(PERMISSOES_FINANCEIRO)
    if perfil in {"ceo", "tech"}:
        return set(CHAVES_PERMISSAO)
    return set(
        permissoes_do_perfil(perfil)
        if perfil != "operador" and not permissoes
        else permissoes
    )


async def _objetos(session: AsyncSession, chaves: set[str]) -> list[PermissaoOperacoes]:
    if not chaves:
        return []
    return list((await session.execute(select(PermissaoOperacoes).where(PermissaoOperacoes.chave.in_(chaves)))).scalars())


def _serializar(u: UsuarioOperacoes, sessoes: int = 0) -> dict:
    return {"id": u.id, "nome": u.nome, "usuario": u.usuario, "email": u.email, "cargo": u.cargo,
            "perfil": u.perfil, "ativo": u.ativo, "alterar_senha": u.alterar_senha,
            "bloqueado_ate": u.bloqueado_ate, "ultimo_login_em": u.ultimo_login_em,
            "permissoes": sorted(p.chave for p in u.permissoes), "sessoes_ativas": sessoes,
            "organizacao_id": u.organizacao_id, "superadmin": u.superadmin}


async def _auditar(session: AsyncSession, ator: UsuarioAutenticado, acao: str, alvo: int, detalhes: dict) -> None:
    session.add(EventoAuditoria(organizacao_id=ator.organizacao_id, actor_id=ator.id, ator=ator.email, acao=acao[:20], recurso=f"usuario:{alvo}",
                                sucesso=True, status_http=200, detalhes=detalhes))


@router.get("")
async def listar(session: SessionDep, ator: ViewDep) -> dict:
    usuarios = list((await session.execute(
        select(UsuarioOperacoes).options(selectinload(UsuarioOperacoes.permissoes))
        .where(UsuarioOperacoes.organizacao_id == ator.organizacao_id)
        .order_by(UsuarioOperacoes.nome)
    )).scalars())
    contagens = dict((await session.execute(select(SessaoOperacoes.usuario_id, func.count()).where(SessaoOperacoes.revogada_em.is_(None), SessaoOperacoes.expira_em > datetime.now(UTC)).group_by(SessaoOperacoes.usuario_id))).all())
    return {"usuarios": [_serializar(u, contagens.get(u.id, 0)) for u in usuarios],
            "permissoes": [{"chave": p.chave, "modulo": p.modulo, "nome": p.nome, "descricao": p.descricao} for p in PERMISSOES],
            "perfis": {nome: sorted(chaves) for nome, chaves in PERFIS.items()}}


@router.post("", status_code=status.HTTP_201_CREATED)
async def criar(dados: UsuarioInput, session: SessionDep, ator: ManageDep) -> dict:
    if dados.perfil in {"administrador", "ceo", "tech"} and ator.perfil not in {
        "administrador",
        "ceo",
        "tech",
    }:
        raise HTTPException(403, "Somente um perfil de acesso total pode atribuir esse perfil")
    await validar_limite_usuarios(session, ator.organizacao_id)
    duplicado = (await session.execute(select(UsuarioOperacoes.id).where(or_(UsuarioOperacoes.usuario == dados.usuario.lower(), UsuarioOperacoes.email == str(dados.email).lower())))).scalar_one_or_none()
    if duplicado:
        raise HTTPException(409, "Usuario ou email ja cadastrado")
    chaves = _validar(dados.perfil, dados.permissoes)
    senha = _temporaria()
    usuario = UsuarioOperacoes(nome=dados.nome.strip(), usuario=dados.usuario.lower(), email=str(dados.email).lower(), cargo=dados.cargo,
        organizacao_id=ator.organizacao_id, perfil=dados.perfil, senha_hash=hash_senha(senha), alterar_senha=True, criado_por=ator.email,
        permissoes=await _objetos(session, chaves))
    session.add(usuario)
    await session.flush()
    await _auditar(session, ator, "CRIAR", usuario.id, {"perfil": usuario.perfil, "permissoes": sorted(chaves)})
    await session.commit()
    return {"usuario": _serializar(usuario), "senha_temporaria": senha}


@router.patch("/{usuario_id}")
async def atualizar(usuario_id: int, dados: UsuarioUpdate, session: SessionDep, ator: ManageDep) -> dict:
    alvo = (await session.execute(select(UsuarioOperacoes).options(selectinload(UsuarioOperacoes.permissoes)).where(
        UsuarioOperacoes.id == usuario_id,
        UsuarioOperacoes.organizacao_id == ator.organizacao_id,
    ))).scalar_one_or_none()
    if not alvo:
        raise HTTPException(404, "Usuario nao encontrado")
    novo_perfil = dados.perfil or alvo.perfil
    if novo_perfil in {"administrador", "ceo", "tech"} and ator.perfil not in {
        "administrador",
        "ceo",
        "tech",
    }:
        raise HTTPException(403, "Somente um perfil de acesso total pode atribuir esse perfil")
    despromove = alvo.perfil == "administrador" and (novo_perfil != "administrador" or dados.ativo is False)
    if alvo.id == ator.id and despromove:
        raise HTTPException(409, "Nao e permitido remover o proprio acesso administrativo")
    if dados.ativo is True and not alvo.ativo:
        await validar_limite_usuarios(session, ator.organizacao_id)
    if despromove:
        total = (await session.execute(select(func.count()).select_from(UsuarioOperacoes).where(
            UsuarioOperacoes.organizacao_id == ator.organizacao_id,
            UsuarioOperacoes.perfil == "administrador",
            UsuarioOperacoes.ativo.is_(True),
        ))).scalar_one()
        if total <= 1:
            raise HTTPException(409, "O sistema deve manter ao menos um administrador ativo")
    if dados.email is not None:
        duplicado = (await session.execute(select(UsuarioOperacoes.id).where(UsuarioOperacoes.email == str(dados.email).lower(), UsuarioOperacoes.id != alvo.id))).scalar_one_or_none()
        if duplicado:
            raise HTTPException(409, "Email ja cadastrado")
    for campo in ("nome", "email", "cargo", "perfil", "ativo"):
        valor = getattr(dados, campo)
        if valor is not None:
            setattr(alvo, campo, str(valor).lower() if campo == "email" else valor)
    if dados.permissoes is not None or dados.perfil is not None:
        alvo.permissoes = await _objetos(session, _validar(novo_perfil, dados.permissoes or []))
    if dados.ativo is False:
        await session.execute(update(SessaoOperacoes).where(SessaoOperacoes.usuario_id == alvo.id, SessaoOperacoes.revogada_em.is_(None)).values(revogada_em=datetime.now(UTC), motivo_revogacao="usuario_bloqueado"))
    await _auditar(session, ator, "ALTERAR", alvo.id, dados.model_dump(exclude_unset=True, mode="json"))
    await session.commit()
    return _serializar(alvo)


@router.post("/{usuario_id}/redefinir-senha")
async def redefinir_senha(usuario_id: int, session: SessionDep, ator: ResetDep) -> dict:
    alvo = (await session.execute(select(UsuarioOperacoes).where(
        UsuarioOperacoes.id == usuario_id,
        UsuarioOperacoes.organizacao_id == ator.organizacao_id,
    ))).scalar_one_or_none()
    if not alvo:
        raise HTTPException(404, "Usuario nao encontrado")
    senha = _temporaria()
    alvo.senha_hash = hash_senha(senha)
    alvo.alterar_senha = True
    await session.execute(update(SessaoOperacoes).where(SessaoOperacoes.usuario_id == alvo.id, SessaoOperacoes.revogada_em.is_(None)).values(revogada_em=datetime.now(UTC), motivo_revogacao="senha_redefinida"))
    await _auditar(session, ator, "REDEFINIR_SENHA", alvo.id, {})
    await session.commit()
    return {"senha_temporaria": senha}


@router.post("/{usuario_id}/revogar-sessoes")
async def revogar_sessoes(usuario_id: int, session: SessionDep, ator: RevokeDep) -> dict:
    alvo = (await session.execute(select(UsuarioOperacoes.id).where(
        UsuarioOperacoes.id == usuario_id,
        UsuarioOperacoes.organizacao_id == ator.organizacao_id,
    ))).scalar_one_or_none()
    if alvo is None:
        raise HTTPException(404, "Usuario nao encontrado")
    resultado = await session.execute(update(SessaoOperacoes).where(SessaoOperacoes.usuario_id == usuario_id, SessaoOperacoes.revogada_em.is_(None)).values(revogada_em=datetime.now(UTC), motivo_revogacao="revogada_administrativamente"))
    await _auditar(session, ator, "REVOGAR_SESSOES", usuario_id, {"quantidade": resultado.rowcount})
    await session.commit()
    return {"revogadas": resultado.rowcount}


@router.post("/{usuario_id}/desbloquear")
async def desbloquear(usuario_id: int, session: SessionDep, ator: ManageDep) -> dict:
    alvo = (await session.execute(select(UsuarioOperacoes).where(
        UsuarioOperacoes.id == usuario_id,
        UsuarioOperacoes.organizacao_id == ator.organizacao_id,
    ))).scalar_one_or_none()
    if not alvo:
        raise HTTPException(404, "Usuario nao encontrado")
    alvo.bloqueado_ate = None
    alvo.tentativas_falhas = 0
    await _auditar(session, ator, "DESBLOQUEAR", alvo.id, {})
    await session.commit()
    return {"desbloqueado": True}

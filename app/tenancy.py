import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models import (
    CredencialIntegracao,
    DominioOrganizacao,
    Organizacao,
    PesquisaMarca,
    UsuarioOperacoes,
)
from app.proxy import host_publico
from app.settings import get_settings


@dataclass(frozen=True)
class OrganizacaoAtual:
    id: int
    nome: str
    slug: str
    plano: str
    modulos: frozenset[str]
    limites: dict
    branding: dict
    politica_privacidade_versao: str = "1.0"


async def aplicar_contexto_tenant(
    session: AsyncSession,
    organizacao_id: int,
    *,
    superadmin: bool = False,
) -> None:
    """Vincula a sessao ao tenant usado pelas politicas RLS do PostgreSQL."""
    if not hasattr(session, "info"):
        return
    session.info["organizacao_id"] = organizacao_id
    session.info["superadmin"] = superadmin
    if session.in_transaction():
        await session.execute(
            select(func.set_config("app.organizacao_id", str(organizacao_id), True))
        )
        await session.execute(
            select(func.set_config("app.superadmin", "true" if superadmin else "false", True))
        )


def _token_requisicao(request: Request) -> str | None:
    token = request.headers.get("X-Integration-Key")
    if token:
        return token.strip()
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def resolver_organizacao_publica(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrganizacaoAtual:
    organizacao = None
    settings = get_settings()
    host = host_publico(request)
    token = _token_requisicao(request)
    if getattr(request.state, "global_integration_token", False) or (
        token
        and settings.integration_auth_enabled
        and secrets.compare_digest(token, settings.inpi_integration_token)
    ):
        padrao = _organizacao_padrao()
        await aplicar_contexto_tenant(session, padrao.id)
        return padrao
    if token and host == "testserver":
        raise HTTPException(401, "Chave de integracao invalida")
    if token:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        credencial = (
            await session.execute(
                select(CredencialIntegracao).where(
                    CredencialIntegracao.token_hash == token_hash,
                    CredencialIntegracao.ativo.is_(True),
                )
            )
        ).scalar_one_or_none()
        if credencial and (
            credencial.expira_em is None or credencial.expira_em > datetime.now(UTC)
        ):
            credencial.ultimo_uso_em = datetime.now(UTC)
            organizacao = (
                await session.execute(
                    select(Organizacao)
                    .options(selectinload(Organizacao.plano))
                    .where(Organizacao.id == credencial.organizacao_id)
                )
            ).scalar_one_or_none()
        if organizacao is None:
            raise HTTPException(401, "Chave de integracao invalida")
    if organizacao is None and host in {"localhost", "127.0.0.1", "testserver"}:
        padrao = _organizacao_padrao()
        await aplicar_contexto_tenant(session, padrao.id)
        return padrao
    if organizacao is None:
        dominio = (
            await session.execute(
                select(DominioOrganizacao).where(
                    DominioOrganizacao.dominio == host,
                    DominioOrganizacao.ativo.is_(True),
                )
            )
        ).scalar_one_or_none()
        if dominio:
            organizacao = (
                await session.execute(
                    select(Organizacao)
                    .options(selectinload(Organizacao.plano))
                    .where(Organizacao.id == dominio.organizacao_id)
                )
            ).scalar_one_or_none()
    if organizacao is None:
        organizacao = (
            await session.execute(
                select(Organizacao)
                .options(selectinload(Organizacao.plano))
                .where(Organizacao.slug == get_settings().default_organization_slug)
            )
        ).scalar_one_or_none()
    if organizacao is None:
        raise HTTPException(503, "Organizacao padrao nao configurada")
    if organizacao.status not in {"ativa", "trial"}:
        raise HTTPException(403, "Organizacao indisponivel")
    atual = OrganizacaoAtual(
        id=organizacao.id,
        nome=organizacao.nome,
        slug=organizacao.slug,
        plano=organizacao.plano.codigo,
        modulos=frozenset(organizacao.plano.modulos or []),
        limites=organizacao.plano.limites or {},
        branding=organizacao.branding or {},
        politica_privacidade_versao=organizacao.politica_privacidade_versao,
    )
    await aplicar_contexto_tenant(session, atual.id)
    return atual


def _organizacao_padrao() -> OrganizacaoAtual:
    settings = get_settings()
    return OrganizacaoAtual(
        id=settings.default_organization_id,
        nome="Zé Registra",
        slug=settings.default_organization_slug,
        plano="profissional",
        modulos=frozenset(
            {
                "consulta",
                "leads",
                "validacao",
                "risco",
                "aprendizado",
                "usuarios",
                "rpi",
                "producao",
                "financeiro",
            }
        ),
        limites={"usuarios": 50, "pesquisas_mes": 10000},
        branding={},
    )


OrganizacaoPublicaDep = Annotated[OrganizacaoAtual, Depends(resolver_organizacao_publica)]


async def validar_limite_pesquisas(session: AsyncSession, organizacao: OrganizacaoAtual) -> None:
    limite = int(organizacao.limites.get("pesquisas_mes", 0) or 0)
    if limite <= 0:
        return
    agora = datetime.now(UTC)
    inicio = datetime(agora.year, agora.month, 1, tzinfo=UTC)
    total = (
        await session.execute(
            select(func.count())
            .select_from(PesquisaMarca)
            .where(
                PesquisaMarca.organizacao_id == organizacao.id,
                PesquisaMarca.criado_em >= inicio,
            )
        )
    ).scalar_one()
    if total >= limite:
        raise HTTPException(429, "Limite mensal de pesquisas atingido")


async def validar_limite_usuarios(session: AsyncSession, organizacao_id: int) -> None:
    organizacao = (
        await session.execute(
            select(Organizacao)
            .options(selectinload(Organizacao.plano))
            .where(Organizacao.id == organizacao_id)
        )
    ).scalar_one_or_none()
    if organizacao is None:
        raise HTTPException(404, "Organizacao nao encontrada")
    limite = int((organizacao.plano.limites or {}).get("usuarios", 0) or 0)
    if limite <= 0:
        return
    total = (
        await session.execute(
            select(func.count())
            .select_from(UsuarioOperacoes)
            .where(
                UsuarioOperacoes.organizacao_id == organizacao_id,
                UsuarioOperacoes.ativo.is_(True),
            )
        )
    ).scalar_one()
    if total >= limite:
        raise HTTPException(409, "Limite de usuarios ativos do plano atingido")

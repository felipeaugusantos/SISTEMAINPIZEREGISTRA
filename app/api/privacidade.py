"""Autoatendimento LGPD: o titular pede a exclusão dos próprios dados sem
depender de um atendente. Mesmo padrão de app/api/auth_routes.py::recuperar_senha
-- token opaco, hash em repouso, expira, resposta sempre genérica (não revela
se o e-mail existe na base)."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_token
from app.database import get_session
from app.emailing import enviar_confirmacao_exclusao
from app.models import Lead, SolicitacaoAnonimizacaoLead
from app.ratelimit import RateLimiter
from app.settings import get_settings
from app.tenancy import OrganizacaoPublicaDep

router = APIRouter(tags=["privacidade"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
limitar_privacidade = RateLimiter(limite=5, janela_segundos=300, escopo="privacidade")

RESPOSTA_GENERICA = {"status": "ok", "mensagem": "Se o e-mail existir na nossa base, você vai receber instruções."}


class SolicitarExclusaoInput(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ConfirmarExclusaoInput(BaseModel):
    token: str = Field(min_length=10, max_length=200)


def _anonimizar_lead(lead: Lead) -> None:
    agora = datetime.now(UTC)
    lead.nome = f"Titular anonimizado ({agora.date().isoformat()})"
    lead.email = f"anonimizado-{lead.id}@removido.zeregistra.local"
    lead.telefone = ""
    lead.documento = None
    lead.empresa = None
    lead.notas = None
    lead.aceite_marketing = False
    lead.anonimizado_em = agora


@router.post("/v1/privacidade/solicitar-exclusao", dependencies=[Depends(limitar_privacidade)])
async def solicitar_exclusao(
    dados: SolicitarExclusaoInput,
    session: SessionDep,
    organizacao: OrganizacaoPublicaDep,
) -> dict:
    email = dados.email.strip().lower()
    tem_lead = (
        await session.execute(
            select(Lead.id).where(
                Lead.organizacao_id == organizacao.id,
                func.lower(Lead.email) == email,
                Lead.anonimizado_em.is_(None),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if tem_lead is not None:
        token = secrets.token_urlsafe(48)
        settings = get_settings()
        await session.execute(
            update(SolicitacaoAnonimizacaoLead)
            .where(
                SolicitacaoAnonimizacaoLead.organizacao_id == organizacao.id,
                SolicitacaoAnonimizacaoLead.email == email,
                SolicitacaoAnonimizacaoLead.usado_em.is_(None),
            )
            .values(usado_em=datetime.now(UTC))
        )
        session.add(
            SolicitacaoAnonimizacaoLead(
                organizacao_id=organizacao.id,
                email=email,
                token_hash=hash_token(token),
                expira_em=datetime.now(UTC) + timedelta(minutes=settings.anonimizacao_token_minutos),
            )
        )
        await session.commit()
        if settings.email_enabled:
            try:
                await enviar_confirmacao_exclusao(email, token)
            except Exception:
                pass
        elif settings.app_env.lower() != "production":
            return {**RESPOSTA_GENERICA, "token_teste_local": token}
    return RESPOSTA_GENERICA


@router.post("/v1/privacidade/confirmar-exclusao")
async def confirmar_exclusao(
    dados: ConfirmarExclusaoInput,
    session: SessionDep,
    organizacao: OrganizacaoPublicaDep,
) -> dict:
    agora = datetime.now(UTC)
    solicitacao = (
        await session.execute(
            select(SolicitacaoAnonimizacaoLead).where(
                SolicitacaoAnonimizacaoLead.organizacao_id == organizacao.id,
                SolicitacaoAnonimizacaoLead.token_hash == hash_token(dados.token),
                SolicitacaoAnonimizacaoLead.usado_em.is_(None),
                SolicitacaoAnonimizacaoLead.expira_em > agora,
            )
        )
    ).scalar_one_or_none()
    if solicitacao is None:
        return {"status": "erro", "mensagem": "Link inválido, já usado ou expirado."}
    leads = list(
        (
            await session.execute(
                select(Lead).where(
                    Lead.organizacao_id == organizacao.id,
                    func.lower(Lead.email) == solicitacao.email,
                    Lead.anonimizado_em.is_(None),
                )
            )
        ).scalars()
    )
    for lead in leads:
        _anonimizar_lead(lead)
    solicitacao.usado_em = agora
    solicitacao.leads_anonimizados = len(leads)
    await session.commit()
    return {"status": "ok", "mensagem": "Seus dados foram removidos.", "registros_afetados": len(leads)}

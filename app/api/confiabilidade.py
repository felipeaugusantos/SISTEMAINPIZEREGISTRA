import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAtualDep, exigir_csrf
from app.database import get_session
from app.models import Lead, Organizacao, PesquisaMarca, SolicitacaoPrivacidade, UsuarioOperacoes
from app.queueing import enfileirar, status_fila

router = APIRouter(prefix="/v1/admin/confiabilidade", tags=["confiabilidade"])
public_router = APIRouter(prefix="/v1/tenant", tags=["tenant"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def exigir_admin(request: Request, usuario: UsuarioAtualDep):
    exigir_csrf(request, usuario)
    if not usuario.pode("production.manage"):
        raise HTTPException(403, "Acesso não autorizado")
    return usuario


AdminDep = Annotated[object, Depends(exigir_admin)]


@public_router.get("/branding")
async def branding_publico(request: Request, session: SessionDep) -> dict:
    from app.tenancy import resolver_organizacao_publica

    org = await resolver_organizacao_publica(request, session)
    return {
        "nome": org.nome,
        "slug": org.slug,
        "branding": org.branding or {},
        "politica_privacidade_versao": org.politica_privacidade_versao,
    }


class ConfiguracaoTenantInput(BaseModel):
    branding: dict = {}
    retencao_dados_dias: int = Field(default=730, ge=30, le=3650)
    politica_privacidade_versao: str = Field(default="1.0", min_length=1, max_length=30)


@router.get("")
async def painel(session: SessionDep, usuario: AdminDep) -> dict:
    org_id = usuario.organizacao_id
    org = await session.get(Organizacao, org_id)
    contagens = {}
    for modelo, chave in (
        (UsuarioOperacoes, "usuarios"),
        (Lead, "leads"),
        (PesquisaMarca, "pesquisas"),
    ):
        contagens[chave] = (
            await session.execute(
                select(func.count()).select_from(modelo).where(modelo.organizacao_id == org_id)
            )
        ).scalar_one()
    return {
        "organizacao": {
            "id": org.id,
            "nome": org.nome,
            "branding": org.branding or {},
            "status": org.status,
            "assinatura_status": org.assinatura_status,
            "trial_ate": org.trial_ate,
            "retencao_dados_dias": org.retencao_dados_dias,
            "politica_privacidade_versao": org.politica_privacidade_versao,
        },
        "uso": contagens,
        "fila": await status_fila(),
    }


@router.patch("/configuracao")
async def configurar(
    dados: ConfiguracaoTenantInput, request: Request, session: SessionDep, usuario: AdminDep
) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    org.branding = dados.branding
    org.retencao_dados_dias = dados.retencao_dados_dias
    org.politica_privacidade_versao = dados.politica_privacidade_versao
    await session.commit()
    return {"status": "ok"}


@router.post("/tarefas/{tipo}", status_code=202)
async def criar_tarefa(tipo: str, request: Request, _: AdminDep) -> dict:
    permitidos = {"assinaturas.verificar", "privacidade.verificar_retencao"}
    if tipo not in permitidos:
        raise HTTPException(422, "Tarefa não permitida")
    try:
        return {"status": "enfileirada", "job": await enfileirar(tipo)}
    except Exception as exc:
        raise HTTPException(503, "Fila indisponível") from exc


@router.post("/privacidade/leads/{lead_id}/solicitar")
async def solicitar_privacidade(
    lead_id: int, request: Request, session: SessionDep, usuario: AdminDep
) -> dict:
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    item = SolicitacaoPrivacidade(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead.id,
        tipo="anonimizacao",
        solicitado_por=usuario.email,
    )
    session.add(item)
    await session.commit()
    return {"id": item.id, "status": item.status}


@router.get("/privacidade/leads/{lead_id}/exportar")
async def exportar_lead(lead_id: int, session: SessionDep, usuario: AdminDep) -> dict:
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    return {
        "nome": lead.nome,
        "email": lead.email,
        "telefone": lead.telefone,
        "empresa": lead.empresa,
        "marca": lead.marca,
        "atividade": lead.atividade,
        "aceite_privacidade": lead.aceite_privacidade,
        "aceite_marketing": lead.aceite_marketing,
        "criado_em": lead.criado_em,
    }


@router.post("/privacidade/solicitacoes/{solicitacao_id}/concluir")
async def anonimizar(
    solicitacao_id: int, request: Request, session: SessionDep, usuario: AdminDep
) -> dict:
    item = (
        await session.execute(
            select(SolicitacaoPrivacidade).where(
                SolicitacaoPrivacidade.id == solicitacao_id,
                SolicitacaoPrivacidade.organizacao_id == usuario.organizacao_id,
                SolicitacaoPrivacidade.status == "aberta",
            )
        )
    ).scalar_one_or_none()
    if not item or not item.lead_id:
        raise HTTPException(404, "Solicitação aberta não encontrada")
    lead = await session.get(Lead, item.lead_id)
    marcador = hashlib.sha256(f"{lead.id}:{lead.email}".encode()).hexdigest()[:12]
    lead.nome = "Titular anonimizado"
    lead.email = f"anonimo-{marcador}@invalid.local"
    lead.telefone = "anonimizado"
    lead.empresa = None
    lead.aceite_marketing = False
    item.status = "concluida"
    item.concluido_por = usuario.email
    item.concluido_em = datetime.now(UTC)
    await session.commit()
    return {"status": "concluida", "lead_id": lead.id}

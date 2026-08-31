import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAtualDep, exigir_csrf
from app.database import get_session
from app.models import (
    AlertaSistema,
    EventoAuditoria,
    Lead,
    Organizacao,
    PesquisaMarca,
    SolicitacaoPrivacidade,
    UsuarioOperacoes,
)
from app.queueing import enfileirar, status_fila
from app.schemas import BrandingConfig
from app.settings import get_settings

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
    settings = get_settings()
    resposta = {
        "nome": org.nome,
        "slug": org.slug,
        "branding": org.branding or {},
        "politica_privacidade_versao": org.politica_privacidade_versao,
    }
    # Identifica o site publico como o tenant padrao junto ao gate de integracao
    # (exigir_token_integracao) do formulario de pesquisa. Nao concede nenhum
    # acesso alem do que este endpoint publico ja resolveu para o visitante.
    if settings.integration_auth_enabled and settings.inpi_integration_token:
        resposta["chave_integracao"] = settings.inpi_integration_token
    return resposta


class ConfiguracaoTenantInput(BaseModel):
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
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
            await session.execute(select(func.count()).select_from(modelo).where(modelo.organizacao_id == org_id))
        ).scalar_one()
    ultima_rotina = (
        await session.execute(
            select(AlertaSistema)
            .where(
                AlertaSistema.organizacao_id == org_id,
                AlertaSistema.codigo.in_(
                    {
                        "PREVISOES_REPROCESSADAS",
                        "AGENTES_REPROCESSADOS",
                        "MODELO_APRENDIZADO_ATIVADO",
                        "MODELO_APRENDIZADO_AGUARDANDO_REVISOES",
                        "MODELO_APRENDIZADO_BLOQUEADO",
                        "MODELO_APRENDIZADO_REPROVADO",
                    }
                ),
            )
            .order_by(AlertaSistema.criado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
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
        "permissoes": {"superadmin": bool(usuario.superadmin)},
        "ultima_rotina_aprendizado": (
            {
                "id": ultima_rotina.id,
                "codigo": ultima_rotina.codigo,
                "severidade": ultima_rotina.severidade,
                "mensagem": ultima_rotina.mensagem,
                "detalhes": ultima_rotina.detalhes or {},
                "criado_em": ultima_rotina.criado_em,
            }
            if ultima_rotina is not None
            else None
        ),
    }


@router.patch("/configuracao")
async def configurar(dados: ConfiguracaoTenantInput, request: Request, session: SessionDep, usuario: AdminDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    org.branding = dados.branding.model_dump(exclude_none=True)
    org.retencao_dados_dias = dados.retencao_dados_dias
    org.politica_privacidade_versao = dados.politica_privacidade_versao
    await session.commit()
    return {"status": "ok"}


@router.post("/tarefas/{tipo}", status_code=202)
async def criar_tarefa(tipo: str, request: Request, usuario: AdminDep) -> dict:
    permitidos = {
        "assinaturas.verificar",
        "privacidade.verificar_retencao",
        "registrabilidade.reconciliar_resultados",
        "registrabilidade.reprocessar_previsoes",
        "registrabilidade.reprocessar_agentes",
        "registrabilidade.pipeline_aprendizado",
    }
    if tipo not in permitidos:
        raise HTTPException(422, "Tarefa não permitida")
    if tipo == "registrabilidade.pipeline_aprendizado" and not usuario.superadmin:
        raise HTTPException(403, "Treinamento global exclusivo do superadministrador")
    payload = {
        "organizacao_id": usuario.organizacao_id,
        "solicitado_por": usuario.email,
    }
    try:
        return {"status": "enfileirada", "job": await enfileirar(tipo, payload)}
    except Exception as exc:
        raise HTTPException(503, "Fila indisponível") from exc


@router.post("/privacidade/leads/{lead_id}/solicitar")
async def solicitar_privacidade(lead_id: int, request: Request, session: SessionDep, usuario: AdminDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
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
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    pesquisas = list(
        (
            await session.execute(
                select(PesquisaMarca)
                .where(
                    PesquisaMarca.lead_id == lead.id,
                    PesquisaMarca.organizacao_id == usuario.organizacao_id,
                )
                .order_by(PesquisaMarca.criado_em.desc())
            )
        ).scalars()
    )
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
        "pesquisas": [
            {
                "id": pesquisa.id,
                "marca": pesquisa.marca,
                "atividade": pesquisa.atividade,
                "tipo_pesquisa": pesquisa.tipo_pesquisa,
                "classe_nice": pesquisa.classe_nice,
                "criado_em": pesquisa.criado_em,
            }
            for pesquisa in pesquisas
        ],
    }


@router.post("/privacidade/solicitacoes/{solicitacao_id}/concluir")
async def anonimizar(solicitacao_id: int, request: Request, session: SessionDep, usuario: AdminDep) -> dict:
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
    if not lead or lead.organizacao_id != usuario.organizacao_id:
        raise HTTPException(404, "Lead nÃ£o encontrado")
    marcador = hashlib.sha256(f"{lead.id}:{lead.email}".encode()).hexdigest()[:12]
    pesquisas_desvinculadas = (
        await session.execute(
            update(PesquisaMarca)
            .where(
                PesquisaMarca.lead_id == lead.id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
            .values(lead_id=None)
        )
    ).rowcount
    lead.nome = "Titular anonimizado"
    lead.email = f"anonimo-{marcador}@invalid.local"
    lead.telefone = "anonimizado"
    lead.empresa = None
    lead.marca = "Interesse anonimizado"
    lead.atividade = None
    lead.processo_numero = None
    lead.aceite_marketing = False
    lead.aceite_privacidade = False
    lead.responsavel_id = None
    lead.notas = None
    lead.proxima_acao_em = None
    lead.ultimo_contato_em = None
    lead.tags = []
    item.status = "concluida"
    item.concluido_por = usuario.email
    item.concluido_em = datetime.now(UTC)
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="ANONIMIZAR",
            recurso="lead",
            sucesso=True,
            status_http=200,
            detalhes={
                "lead_id": lead.id,
                "pesquisas_desvinculadas": pesquisas_desvinculadas or 0,
            },
        )
    )
    await session.commit()
    return {"status": "concluida", "lead_id": lead.id}

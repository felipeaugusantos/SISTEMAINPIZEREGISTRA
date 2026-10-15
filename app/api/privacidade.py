"""Autoatendimento LGPD: o titular pede a exclusão dos próprios dados sem
depender de um atendente. Mesmo padrão de app/api/auth_routes.py::recuperar_senha
-- token opaco, hash em repouso, expira, resposta sempre genérica (não revela
se o e-mail existe na base)."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip, hash_token
from app.database import get_session
from app.emailing import enviar_confirmacao_exclusao
from app.marca import nome_escritorio_para_email
from app.models import ArquivoClientePortal, EventoAuditoria, Lead, Organizacao, SolicitacaoAnonimizacaoLead
from app.proxy import cliente_ip
from app.ratelimit import RateLimiter
from app.retencao import motivos_bloqueio_lead, prazo_retencao_vigente
from app.settings import get_settings
from app.storage import delete_object
from app.tenancy import OrganizacaoPublicaDep

router = APIRouter(tags=["privacidade"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LeadsDeleteDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.delete"))]
limitar_privacidade = RateLimiter(limite=5, janela_segundos=300, escopo="privacidade")
limitar_confirmacao_privacidade = RateLimiter(limite=10, janela_segundos=300, escopo="privacidade-confirmar")

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
        # Fase 19.3: resolvido antes do commit, ainda no contexto do tenant.
        nome_escritorio = await nome_escritorio_para_email(session, organizacao.id)
        await session.commit()
        if settings.email_enabled:
            try:
                await enviar_confirmacao_exclusao(email, token, organizacao_nome=nome_escritorio)
            except Exception:
                pass
        elif settings.app_env.lower() != "production":
            return {**RESPOSTA_GENERICA, "token_teste_local": token}
    return RESPOSTA_GENERICA


@router.post("/v1/privacidade/confirmar-exclusao", dependencies=[Depends(limitar_confirmacao_privacidade)])
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
    # Auditoria A07 (15/10/2026): o autoatendimento anonimizava sem consultar as regras de retenção e sem
    # apagar os arquivos do portal. Agora usa as mesmas regras do descarte administrativo: o que tem
    # bloqueio (atendimento/processo/contrato ativo, documento obrigatório, legal hold) é conservado e
    # informado ao titular; o restante tem os arquivos físicos apagados e o cadastro anonimizado.
    anonimizados = 0
    arquivos_removidos = 0
    conservados: list[dict] = []
    try:
        for lead in leads:
            bloqueios = await motivos_bloqueio_lead(session, lead)
            if bloqueios:
                conservados.append({"lead_id": lead.id, "motivos": bloqueios})
                continue
            arquivos = (
                (
                    await session.execute(
                        select(ArquivoClientePortal).where(
                            ArquivoClientePortal.lead_id == lead.id,
                            ArquivoClientePortal.organizacao_id == organizacao.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for arquivo in arquivos:
                delete_object(arquivo.caminho)
            for arquivo in arquivos:
                await session.delete(arquivo)
            arquivos_removidos += len(arquivos)
            _anonimizar_lead(lead)
            anonimizados += 1
    except Exception:
        # Falha no storage: nada é confirmado e o link continua válido para o titular tentar de novo.
        # Arquivos já apagados não voltam; a nova tentativa os trata como inexistentes (idempotente).
        await session.rollback()
        raise HTTPException(503, "Não foi possível concluir a exclusão agora. Tente novamente em instantes.") from None
    solicitacao.usado_em = agora
    solicitacao.leads_anonimizados = anonimizados
    session.add(
        EventoAuditoria(
            organizacao_id=organizacao.id,
            ator="titular",
            acao="exclusao_titular",
            recurso=f"solicitacao:{solicitacao.id}",
            sucesso=True,
            status_http=200,
            detalhes={
                "anonimizados": anonimizados,
                "conservados": [{"lead_id": c["lead_id"], "motivos": c["motivos"]} for c in conservados],
                "arquivos_removidos": arquivos_removidos,
            },
        )
    )
    await session.commit()
    if conservados:
        mensagem = (
            f"Removemos {anonimizados} registro(s). {len(conservados)} registro(s) foram mantidos por "
            "obrigação legal ou atendimento em andamento e serão tratados pelo escritório."
            if anonimizados
            else "Seus dados não puderam ser removidos agora: há atendimento, contrato, processo ou "
            "obrigação legal em andamento. O escritório foi notificado e tratará a solicitação."
        )
        return {
            "status": "parcial" if anonimizados else "conservado",
            "mensagem": mensagem,
            "registros_afetados": anonimizados,
            "registros_conservados": len(conservados),
        }
    return {"status": "ok", "mensagem": "Seus dados foram removidos.", "registros_afetados": anonimizados}


# --- Achado FASE6-14 da auditoria (04/09/2026): retenção -- app/worker.py
# (tarefa "privacidade.verificar_retencao") já detecta leads que excedem
# Organizacao.retencao_dados_dias e cria um AlertaSistema("RETENCAO_PENDENTE")
# para revisão humana, mas não existia nenhum jeito de um humano CONFIRMAR o
# descarte -- nem via API. "Descarte seguro" aqui significa dois passos:
# anonimizar o Lead (mesmo _anonimizar_lead do autoatendimento) e apagar de
# verdade os arquivos físicos do portal do cliente vinculados a ele (não só
# a linha do banco) via app.storage.delete_object. Nunca automático: sempre
# um humano confirmando um lead por vez, e só depois do prazo de retenção já
# ter vencido de verdade (verificado no servidor, não confiado do cliente).
# ---


@router.post("/v1/admin/privacidade/leads/{lead_id}/descartar")
async def descartar_lead_por_retencao(
    lead_id: int, request: Request, session: SessionDep, usuario: LeadsDeleteDep
) -> dict:
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(404, "Lead não encontrado")
    if lead.anonimizado_em is not None:
        return {"anonimizado": True, "arquivos_removidos": 0, "ja_processado": True}
    org = await session.get(Organizacao, usuario.organizacao_id)
    prazo_dias = await prazo_retencao_vigente(session, org)
    limite = datetime.now(UTC) - timedelta(days=prazo_dias)
    if lead.criado_em >= limite:
        raise HTTPException(
            422,
            "Lead ainda está dentro do prazo de retenção configurado -- descarte manual só é "
            "permitido depois que o prazo vence.",
        )
    bloqueios = await motivos_bloqueio_lead(session, lead)
    if bloqueios:
        raise HTTPException(
            409,
            {
                "mensagem": "Lead protegido contra descarte por retenção.",
                "bloqueios": bloqueios,
            },
        )

    arquivos = (
        (
            await session.execute(
                select(ArquivoClientePortal).where(
                    ArquivoClientePortal.lead_id == lead_id,
                    ArquivoClientePortal.organizacao_id == usuario.organizacao_id,
                )
            )
        )
        .scalars()
        .all()
    )
    try:
        for arquivo in arquivos:
            delete_object(arquivo.caminho)
    except Exception as exc:
        await session.rollback()
        session.add(
            EventoAuditoria(
                organizacao_id=usuario.organizacao_id,
                actor_id=usuario.id,
                ator=usuario.ator,
                acao="descartar_lead",
                recurso=f"lead:{lead_id}",
                sucesso=False,
                status_http=503,
                ip_hash=hash_ip(cliente_ip(request)),
                detalhes={"erro": "falha_remocao_arquivo", "tipo": type(exc).__name__},
            )
        )
        await session.commit()
        raise HTTPException(503, "Não foi possível remover os arquivos; o lead não foi anonimizado.") from exc

    for arquivo in arquivos:
        await session.delete(arquivo)

    _anonimizar_lead(lead)
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao="descartar_lead",
            recurso=f"lead:{lead_id}",
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes={"arquivos_removidos": len(arquivos)},
        )
    )
    await session.commit()
    return {"anonimizado": True, "arquivos_removidos": len(arquivos), "ja_processado": False}

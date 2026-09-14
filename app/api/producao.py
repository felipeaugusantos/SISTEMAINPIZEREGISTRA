from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.saas import SuperAdminDep
from app.auditing import criar_evento_auditoria
from app.auth import UsuarioAtualDep, UsuarioAutenticado, exigir_csrf, exigir_permissao, hash_ip
from app.avisos_versao import confirmar_leitura
from app.database import get_session
from app.models import (
    AvaliacaoRiscoMarca,
    AvisoVersao,
    AvisoVersaoConfirmacao,
    EventoAuditoria,
    EventoOperacional,
    PesquisaMarca,
    UsuarioOperacoes,
    VersaoRelatorioMarca,
)
from app.proxy import cliente_ip
from app.schemas import (
    AvisoVersaoConfirmacaoResponse,
    AvisoVersaoCreate,
    AvisoVersaoResponse,
    EventoAuditoriaResponse,
    ProducaoAdminResponse,
)
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin/producao", tags=["governança de produção"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("production.view"))]
AvisosDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("avisos.view"))]


async def _resumo(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    limite_auditoria: int = 10,
    deslocamento_auditoria: int = 0,
) -> ProducaoAdminResponse:
    settings = get_settings()
    desde = datetime.now(UTC) - timedelta(hours=24)
    operacional = (
        await session.execute(
            select(
                func.count(),
                func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0)),
                func.avg(EventoOperacional.duracao_ms),
                func.percentile_cont(0.95).within_group(EventoOperacional.duracao_ms),
                func.max(EventoOperacional.duracao_ms),
            ).where(EventoOperacional.criado_em >= desde)
        )
    ).one()
    if not usuario.superadmin:
        operacional = (0, 0, 0, 0, 0)
    comparacao = (
        await session.execute(
            select(
                func.count(),
                func.sum(
                    case(
                        (
                            AvaliacaoRiscoMarca.nivel != AvaliacaoRiscoMarca.nivel_humano,
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(
                AvaliacaoRiscoMarca.nivel_humano.is_not(None),
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).one()
    versoes = (
        await session.execute(
            select(
                func.count(),
                func.count(distinct(VersaoRelatorioMarca.pesquisa_id)),
            )
            .join(PesquisaMarca, PesquisaMarca.id == VersaoRelatorioMarca.pesquisa_id)
            .where(PesquisaMarca.organizacao_id == usuario.organizacao_id)
        )
    ).one()
    auditoria_total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(EventoAuditoria)
                .where(EventoAuditoria.organizacao_id == usuario.organizacao_id)
            )
        ).scalar_one()
    )
    auditoria = (
        (
            await session.execute(
                select(EventoAuditoria)
                .where(EventoAuditoria.organizacao_id == usuario.organizacao_id)
                .order_by(EventoAuditoria.criado_em.desc(), EventoAuditoria.id.desc())
                .limit(limite_auditoria)
                .offset(deslocamento_auditoria)
            )
        )
        .scalars()
        .all()
    )
    requisicoes = int(operacional[0] or 0)
    erros = int(operacional[1] or 0)
    avaliadas = int(comparacao[0] or 0)
    divergencias = int(comparacao[1] or 0)
    return ProducaoAdminResponse(
        ambiente=settings.app_env,
        requisicoes_24h=requisicoes,
        erros_24h=erros,
        taxa_erros_24h=erros / requisicoes if requisicoes else 0,
        duracao_media_ms_24h=float(operacional[2] or 0),
        duracao_p95_ms_24h=float(operacional[3] or 0),
        duracao_maxima_ms_24h=int(operacional[4] or 0),
        avaliacoes_humanas=avaliadas,
        divergencias_humanas=divergencias,
        taxa_divergencia=divergencias / avaliadas if avaliadas else 0,
        relatorios_versionados=int(versoes[0] or 0),
        pesquisas_com_versao=int(versoes[1] or 0),
        auditoria_total=auditoria_total,
        auditoria_limite=limite_auditoria,
        auditoria_deslocamento=deslocamento_auditoria,
        auditoria=[
            EventoAuditoriaResponse(
                request_id=item.request_id,
                actor_id=item.actor_id,
                ator=item.ator,
                acao=item.acao,
                recurso=item.recurso,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                sucesso=item.sucesso,
                status_http=item.status_http,
                before_state=item.before_state,
                after_state=item.after_state,
                criado_em=item.criado_em,
            )
            for item in auditoria
        ],
    )


@router.get("", response_model=ProducaoAdminResponse)
async def obter_producao(
    session: SessionDep,
    usuario: AdminDep,
    limite_auditoria: Annotated[int, Query(ge=1, le=100)] = 10,
    deslocamento_auditoria: Annotated[int, Query(ge=0)] = 0,
) -> ProducaoAdminResponse:
    resposta = await _resumo(
        session,
        usuario,
        limite_auditoria=limite_auditoria,
        deslocamento_auditoria=deslocamento_auditoria,
    )
    if not usuario.pode("audit.view"):
        resposta.auditoria = []
        resposta.auditoria_total = 0
        resposta.auditoria_deslocamento = 0
    return resposta


async def _resposta_aviso(
    session: AsyncSession, usuario: UsuarioAutenticado, aviso: AvisoVersao
) -> AvisoVersaoResponse:
    filtro_usuarios = [UsuarioOperacoes.ativo.is_(True)]
    if aviso.organizacao_id is not None:
        filtro_usuarios.append(UsuarioOperacoes.organizacao_id == aviso.organizacao_id)
    total_usuarios = int(
        (await session.execute(select(func.count()).select_from(UsuarioOperacoes).where(*filtro_usuarios))).scalar_one()
    )
    total_confirmados = int(
        (
            await session.execute(
                select(func.count())
                .select_from(AvisoVersaoConfirmacao)
                .where(AvisoVersaoConfirmacao.aviso_id == aviso.id)
            )
        ).scalar_one()
    )
    confirmado_por_mim = (
        await session.execute(
            select(AvisoVersaoConfirmacao.id).where(
                AvisoVersaoConfirmacao.aviso_id == aviso.id,
                AvisoVersaoConfirmacao.usuario_id == usuario.id,
            )
        )
    ).scalar_one_or_none() is not None
    return AvisoVersaoResponse(
        id=aviso.id,
        versao=aviso.versao,
        titulo=aviso.titulo,
        mensagem=aviso.mensagem,
        severidade=aviso.severidade,
        critico=aviso.critico,
        ativo=aviso.ativo,
        publicado_em=aviso.publicado_em,
        total_usuarios=total_usuarios,
        total_confirmados=total_confirmados,
        pendentes=max(total_usuarios - total_confirmados, 0),
        confirmado_por_mim=confirmado_por_mim,
    )


@router.get("/avisos", response_model=list[AvisoVersaoResponse])
async def listar_avisos_versao(session: SessionDep, usuario: AvisosDep) -> list[AvisoVersaoResponse]:
    avisos = (
        (
            await session.execute(
                select(AvisoVersao).where(AvisoVersao.ativo.is_(True)).order_by(AvisoVersao.publicado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return [await _resposta_aviso(session, usuario, aviso) for aviso in avisos]


@router.get("/avisos/pendentes", response_model=list[AvisoVersaoResponse])
async def listar_avisos_pendentes(session: SessionDep, usuario: UsuarioAtualDep) -> list[AvisoVersaoResponse]:
    """Fonte do banner global (app/web/static/admin-shell.js): avisos ativos
    que este usuário ainda não confirmou."""
    avisos = (
        (
            await session.execute(
                select(AvisoVersao)
                .outerjoin(
                    AvisoVersaoConfirmacao,
                    (AvisoVersaoConfirmacao.aviso_id == AvisoVersao.id)
                    & (AvisoVersaoConfirmacao.usuario_id == usuario.id),
                )
                .where(AvisoVersao.ativo.is_(True), AvisoVersaoConfirmacao.id.is_(None))
                .order_by(AvisoVersao.critico.desc(), AvisoVersao.publicado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return [await _resposta_aviso(session, usuario, aviso) for aviso in avisos]


@router.post("/avisos", response_model=AvisoVersaoResponse, status_code=201)
async def criar_aviso_versao(
    dados: AvisoVersaoCreate,
    request: Request,
    session: SessionDep,
    usuario: SuperAdminDep,
) -> AvisoVersaoResponse:
    aviso = AvisoVersao(
        organizacao_id=None,
        versao=dados.versao,
        titulo=dados.titulo,
        mensagem=dados.mensagem,
        severidade=dados.severidade,
        critico=dados.critico,
        criado_por_id=usuario.id,
    )
    session.add(aviso)
    await session.flush()
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            ator=usuario.ator,
            actor_id=usuario.id,
            acao="AVISO_PUBLICADO",
            recurso="aviso_versao",
            resource_type="aviso_versao",
            resource_id=aviso.id,
            sucesso=True,
            status_http=201,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes={"versao": aviso.versao, "critico": aviso.critico},
        )
    )
    await session.commit()
    return await _resposta_aviso(session, usuario, aviso)


@router.get("/avisos/{aviso_id}/confirmacoes", response_model=list[AvisoVersaoConfirmacaoResponse])
async def listar_confirmacoes_aviso(
    aviso_id: int, session: SessionDep, usuario: AvisosDep
) -> list[AvisoVersaoConfirmacaoResponse]:
    """Quem confirmou (e quem ainda não confirmou) um aviso -- RLS restringe
    a lista de usuários à organização de quem consulta, exceto superadmin.
    Atende o critério de aceite: administradores acompanham quem tomou
    conhecimento da atualização."""
    aviso = await session.get(AvisoVersao, aviso_id)
    if aviso is None:
        raise HTTPException(404, "Aviso não encontrado")
    filtro_usuarios = [UsuarioOperacoes.ativo.is_(True)]
    if aviso.organizacao_id is not None:
        filtro_usuarios.append(UsuarioOperacoes.organizacao_id == aviso.organizacao_id)
    linhas = (
        await session.execute(
            select(
                UsuarioOperacoes.id,
                UsuarioOperacoes.nome,
                UsuarioOperacoes.email,
                AvisoVersaoConfirmacao.confirmado_em,
            )
            .outerjoin(
                AvisoVersaoConfirmacao,
                (AvisoVersaoConfirmacao.usuario_id == UsuarioOperacoes.id)
                & (AvisoVersaoConfirmacao.aviso_id == aviso_id),
            )
            .where(*filtro_usuarios)
            .order_by(AvisoVersaoConfirmacao.confirmado_em.is_(None), UsuarioOperacoes.nome)
        )
    ).all()
    return [
        AvisoVersaoConfirmacaoResponse(usuario_id=usuario_id, nome=nome, email=email, confirmado_em=confirmado_em)
        for usuario_id, nome, email, confirmado_em in linhas
    ]


@router.post("/avisos/{aviso_id}/confirmar", response_model=AvisoVersaoResponse)
async def confirmar_leitura_aviso(
    aviso_id: int, request: Request, session: SessionDep, usuario: UsuarioAtualDep
) -> AvisoVersaoResponse:
    """Confirmação individual de leitura -- ação puramente passiva, nunca
    aciona deploy/rollout. Sem endpoint de recusa: um aviso crítico só sai
    do banner do usuário quando confirmado."""
    exigir_csrf(request, usuario)
    aviso = await session.get(AvisoVersao, aviso_id)
    if aviso is None or not aviso.ativo:
        raise HTTPException(404, "Aviso não encontrado")
    await confirmar_leitura(
        session,
        aviso_id=aviso_id,
        usuario_id=usuario.id,
        organizacao_id=usuario.organizacao_id,
        ip_hash=hash_ip(cliente_ip(request)),
    )
    session.add(
        criar_evento_auditoria(
            organizacao_id=usuario.organizacao_id,
            ator=usuario.ator,
            actor_id=usuario.id,
            acao="LEITURA_AVISO",
            recurso=f"aviso_versao:{aviso_id}",
            resource_type="aviso_versao",
            resource_id=aviso_id,
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes={"versao": aviso.versao},
        )
    )
    await session.commit()
    return await _resposta_aviso(session, usuario, aviso)

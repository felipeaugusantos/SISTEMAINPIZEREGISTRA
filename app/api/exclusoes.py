from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    AcaoAdminDep,
    UsuarioAutenticado,
    exigir_permissao,
    fora_do_loop,
    hash_ip,
    verificar_senha,
)
from app.database import get_session
from app.models import (
    EventoAuditoria,
    Lead,
    PesquisaMarca,
    SolicitacaoExclusaoLead,
    SolicitacaoExclusaoPesquisa,
    UsuarioOperacoes,
)
from app.proxy import cliente_ip

router = APIRouter(tags=["exclusao de pesquisas"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
PesquisaViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
PesquisaDeleteDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.delete"))]


class SolicitarExclusaoInput(BaseModel):
    motivo: str = Field(min_length=3, max_length=1000)

    @field_validator("motivo")
    @classmethod
    def limpar_motivo(cls, valor: str) -> str:
        return valor.strip()


class ConfirmarExclusaoInput(BaseModel):
    senha: str = Field(min_length=1, max_length=200)
    motivo: str | None = Field(default=None, max_length=1000)

    @field_validator("motivo")
    @classmethod
    def limpar_motivo(cls, valor: str | None) -> str | None:
        return valor.strip() or None if valor else None


class DecidirExclusaoInput(BaseModel):
    decisao: Literal["aprovar", "rejeitar"]
    senha: str | None = Field(default=None, max_length=200)
    observacao: str | None = Field(default=None, max_length=1000)

    @field_validator("observacao")
    @classmethod
    def limpar_observacao(cls, valor: str | None) -> str | None:
        return valor.strip() or None if valor else None


class DecidirLoteInput(DecidirExclusaoInput):
    ids: list[int] = Field(min_length=1, max_length=100)


def _auditar(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    request: Request,
    acao: str,
    recurso: str,
    sucesso: bool,
    status_http: int,
    detalhes: dict,
) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao=acao[:20],
            recurso=recurso[:180],
            sucesso=sucesso,
            status_http=status_http,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes=detalhes,
        )
    )


async def _pesquisa(session: AsyncSession, usuario: UsuarioAutenticado, pesquisa_id: str) -> PesquisaMarca:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca)
            .where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    return pesquisa


async def _exigir_senha(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    senha: str | None,
    request: Request,
    recurso: str,
) -> None:
    registro = await session.get(UsuarioOperacoes, usuario.id)
    if not senha or registro is None or not await fora_do_loop(verificar_senha, registro.senha_hash, senha):
        _auditar(
            session,
            usuario,
            request,
            "exclusao_negada",
            recurso,
            False,
            403,
            {"motivo": "senha_invalida"},
        )
        await session.commit()
        raise HTTPException(status_code=403, detail="Senha atual inválida")


def _solicitacao_response(item: SolicitacaoExclusaoPesquisa) -> dict:
    return {
        "id": item.id,
        "pesquisa_id": item.pesquisa_id,
        "pesquisa_referencia": item.pesquisa_referencia,
        "marca": item.marca,
        "lead_id": item.lead_id,
        "solicitado_por": item.solicitado_por,
        "motivo": item.motivo,
        "status": item.status,
        "decidido_por": item.decidido_por,
        "decisao_observacao": item.decisao_observacao,
        "criado_em": item.criado_em,
        "decidido_em": item.decidido_em,
    }


@router.post(
    "/v1/admin/pesquisas/{pesquisa_id}/solicitar-exclusao",
    status_code=status.HTTP_201_CREATED,
)
async def solicitar_exclusao(
    pesquisa_id: str,
    dados: SolicitarExclusaoInput,
    request: Request,
    session: SessionDep,
    usuario: PesquisaViewDep,
    _limite: AcaoAdminDep,
) -> dict:
    pesquisa = await _pesquisa(session, usuario, pesquisa_id)
    existente = (
        await session.execute(
            select(SolicitacaoExclusaoPesquisa).where(
                SolicitacaoExclusaoPesquisa.organizacao_id == usuario.organizacao_id,
                SolicitacaoExclusaoPesquisa.pesquisa_id == pesquisa.id,
                SolicitacaoExclusaoPesquisa.status == "pendente",
            )
        )
    ).scalar_one_or_none()
    if existente is not None:
        return _solicitacao_response(existente)
    item = SolicitacaoExclusaoPesquisa(
        organizacao_id=usuario.organizacao_id,
        pesquisa_id=pesquisa.id,
        pesquisa_referencia=pesquisa.id,
        marca=pesquisa.marca,
        lead_id=pesquisa.lead_id,
        solicitado_por_id=usuario.id,
        solicitado_por=usuario.ator,
        motivo=dados.motivo,
        status="pendente",
    )
    session.add(item)
    _auditar(
        session,
        usuario,
        request,
        "solicitar_exclusao",
        f"pesquisa:{pesquisa.id}",
        True,
        201,
        {"marca": pesquisa.marca, "motivo": dados.motivo},
    )
    await session.commit()
    await session.refresh(item)
    return _solicitacao_response(item)


@router.delete("/v1/admin/pesquisas/{pesquisa_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_pesquisa(
    pesquisa_id: str,
    dados: ConfirmarExclusaoInput,
    request: Request,
    session: SessionDep,
    usuario: PesquisaDeleteDep,
    _limite: AcaoAdminDep,
) -> Response:
    pesquisa = await _pesquisa(session, usuario, pesquisa_id)
    await _exigir_senha(session, usuario, dados.senha, request, f"pesquisa:{pesquisa.id}")
    registro = (
        await session.execute(
            select(SolicitacaoExclusaoPesquisa)
            .where(
                SolicitacaoExclusaoPesquisa.organizacao_id == usuario.organizacao_id,
                SolicitacaoExclusaoPesquisa.pesquisa_id == pesquisa.id,
                SolicitacaoExclusaoPesquisa.status == "pendente",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if registro is None:
        registro = SolicitacaoExclusaoPesquisa(
            organizacao_id=usuario.organizacao_id,
            pesquisa_id=pesquisa.id,
            pesquisa_referencia=pesquisa.id,
            marca=pesquisa.marca,
            lead_id=pesquisa.lead_id,
            solicitado_por_id=usuario.id,
            solicitado_por=usuario.ator,
            motivo=dados.motivo or "Exclusão direta confirmada pelo administrador",
        )
        session.add(registro)
    registro.status = "executada"
    registro.decidido_por_id = usuario.id
    registro.decidido_por = usuario.ator
    registro.decisao_observacao = dados.motivo
    registro.decidido_em = datetime.now(UTC)
    _auditar(
        session,
        usuario,
        request,
        "excluir_pesquisa",
        f"pesquisa:{pesquisa.id}",
        True,
        204,
        {"marca": pesquisa.marca, "lead_id": pesquisa.lead_id, "modo": "direto"},
    )
    await session.delete(pesquisa)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/v1/admin/exclusoes-pesquisas")
async def listar_solicitacoes(
    session: SessionDep,
    usuario: PesquisaDeleteDep,
    status_solicitacao: str = "pendente",
    limite: Annotated[int, Query(ge=1, le=200)] = 100,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
    busca: Annotated[str, Query(max_length=100)] = "",
) -> dict:
    filtros = [SolicitacaoExclusaoPesquisa.organizacao_id == usuario.organizacao_id]
    if status_solicitacao:
        filtros.append(SolicitacaoExclusaoPesquisa.status == status_solicitacao)
    termo = busca.strip()
    if termo:
        padrao = "%" + termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        filtros.append(
            or_(
                SolicitacaoExclusaoPesquisa.marca.ilike(padrao, escape="\\"),
                SolicitacaoExclusaoPesquisa.solicitado_por.ilike(padrao, escape="\\"),
                SolicitacaoExclusaoPesquisa.motivo.ilike(padrao, escape="\\"),
            )
        )
    total = (
        await session.execute(select(func.count()).select_from(SolicitacaoExclusaoPesquisa).where(*filtros))
    ).scalar_one()
    itens = (
        (
            await session.execute(
                select(SolicitacaoExclusaoPesquisa)
                .where(*filtros)
                .order_by(SolicitacaoExclusaoPesquisa.criado_em.desc(), SolicitacaoExclusaoPesquisa.id.desc())
                .limit(limite)
                .offset(deslocamento)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total": total,
        "exibidas": len(itens),
        "itens": [_solicitacao_response(item) for item in itens],
    }


@router.post("/v1/admin/exclusoes-pesquisas/decidir-lote")
async def decidir_solicitacoes_em_lote(
    dados: DecidirLoteInput,
    request: Request,
    session: SessionDep,
    usuario: PesquisaDeleteDep,
    _limite: AcaoAdminDep,
) -> dict:
    ids = list(dict.fromkeys(dados.ids))
    itens = (
        (
            await session.execute(
                select(SolicitacaoExclusaoPesquisa)
                .where(
                    SolicitacaoExclusaoPesquisa.id.in_(ids),
                    SolicitacaoExclusaoPesquisa.organizacao_id == usuario.organizacao_id,
                    SolicitacaoExclusaoPesquisa.status == "pendente",
                )
                .order_by(SolicitacaoExclusaoPesquisa.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if dados.decisao == "aprovar":
        await _exigir_senha(session, usuario, dados.senha, request, "pesquisas:lote")
    agora = datetime.now(UTC)
    aprovar = dados.decisao == "aprovar"
    processadas = 0
    ja_removidas = 0
    ignoradas = len(ids) - len(itens)
    for item in itens:
        pesquisa = None
        if aprovar and item.pesquisa_id is not None:
            pesquisa = (
                await session.execute(
                    select(PesquisaMarca)
                    .where(
                        PesquisaMarca.id == item.pesquisa_id,
                        PesquisaMarca.organizacao_id == usuario.organizacao_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
        # Aprovar uma solicitação cuja pesquisa já foi removida por outro caminho a encerra como
        # executada: do contrário ela ficaria pendente na fila para sempre.
        orfa = aprovar and pesquisa is None
        item.status = "executada" if aprovar else "rejeitada"
        item.decidido_por_id = usuario.id
        item.decidido_por = usuario.ator
        item.decisao_observacao = "Pesquisa já havia sido removida" if orfa else dados.observacao
        item.decidido_em = agora
        _auditar(
            session,
            usuario,
            request,
            "aprovar_exclusao" if aprovar else "rejeitar_exclusao",
            f"pesquisa:{item.pesquisa_referencia}",
            True,
            200,
            {"solicitacao_id": item.id, "marca": item.marca, "modo": "lote", "pesquisa_ja_removida": orfa},
        )
        if pesquisa is not None:
            await session.delete(pesquisa)
        ja_removidas += int(orfa)
        processadas += 1
    await session.commit()
    return {
        "decisao": dados.decisao,
        "processadas": processadas,
        "ja_removidas": ja_removidas,
        "ignoradas": ignoradas,
    }


@router.post("/v1/admin/exclusoes-pesquisas/{solicitacao_id}/decidir")
async def decidir_solicitacao(
    solicitacao_id: int,
    dados: DecidirExclusaoInput,
    request: Request,
    session: SessionDep,
    usuario: PesquisaDeleteDep,
    _limite: AcaoAdminDep,
) -> dict:
    item = (
        await session.execute(
            select(SolicitacaoExclusaoPesquisa)
            .where(
                SolicitacaoExclusaoPesquisa.id == solicitacao_id,
                SolicitacaoExclusaoPesquisa.organizacao_id == usuario.organizacao_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    if item.status != "pendente":
        raise HTTPException(status_code=409, detail="Solicitação já foi decidida")
    agora = datetime.now(UTC)
    if dados.decisao == "rejeitar":
        item.status = "rejeitada"
        item.decidido_por_id = usuario.id
        item.decidido_por = usuario.ator
        item.decisao_observacao = dados.observacao
        item.decidido_em = agora
        _auditar(
            session,
            usuario,
            request,
            "rejeitar_exclusao",
            f"pesquisa:{item.pesquisa_referencia}",
            True,
            200,
            {"solicitacao_id": item.id, "observacao": dados.observacao},
        )
        await session.commit()
        return _solicitacao_response(item)

    await _exigir_senha(
        session,
        usuario,
        dados.senha,
        request,
        f"pesquisa:{item.pesquisa_referencia}",
    )
    if item.pesquisa_id is None:
        raise HTTPException(status_code=409, detail="A pesquisa já não existe")
    pesquisa = await _pesquisa(session, usuario, item.pesquisa_id)
    item.status = "executada"
    item.decidido_por_id = usuario.id
    item.decidido_por = usuario.ator
    item.decisao_observacao = dados.observacao
    item.decidido_em = agora
    _auditar(
        session,
        usuario,
        request,
        "aprovar_exclusao",
        f"pesquisa:{pesquisa.id}",
        True,
        200,
        {"solicitacao_id": item.id, "marca": pesquisa.marca},
    )
    await session.delete(pesquisa)
    await session.commit()
    return _solicitacao_response(item)


# ---------------------------------------------------------------------------
# Pedido de exclusão de CONTATO (lead). Distinto de descartar a oportunidade e de excluir
# pesquisa. Nada é apagado: aprovar ARQUIVA o lead (restaurável, pesquisas preservadas).
# ---------------------------------------------------------------------------


def _solicitacao_lead_response(item: SolicitacaoExclusaoLead) -> dict:
    return {
        "tipo": "lead",
        "id": item.id,
        "lead_id": item.lead_id,
        "marca": item.lead_nome,
        "solicitado_por": item.solicitado_por,
        "motivo": item.motivo,
        "status": item.status,
        "decidido_por": item.decidido_por,
        "decisao_observacao": item.decisao_observacao,
        "criado_em": item.criado_em,
        "decidido_em": item.decidido_em,
    }


async def _lead_ativo(session: AsyncSession, usuario: UsuarioAutenticado, lead_id: int) -> Lead | None:
    return (
        await session.execute(
            select(Lead)
            .where(
                Lead.id == lead_id,
                Lead.organizacao_id == usuario.organizacao_id,
                Lead.arquivado_em.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _pendente_lead(
    session: AsyncSession, usuario: UsuarioAutenticado, lead_id: int
) -> SolicitacaoExclusaoLead | None:
    return (
        await session.execute(
            select(SolicitacaoExclusaoLead).where(
                SolicitacaoExclusaoLead.organizacao_id == usuario.organizacao_id,
                SolicitacaoExclusaoLead.lead_id == lead_id,
                SolicitacaoExclusaoLead.status == "pendente",
            )
        )
    ).scalar_one_or_none()


@router.post("/v1/admin/leads/{lead_id}/solicitar-exclusao", status_code=status.HTTP_201_CREATED)
async def solicitar_exclusao_lead(
    lead_id: int,
    dados: SolicitarExclusaoInput,
    request: Request,
    response: Response,
    session: SessionDep,
    usuario: PesquisaViewDep,
    _limite: AcaoAdminDep,
) -> dict:
    """Registra o pedido na fila de aprovação. Não exclui nem arquiva nada."""
    lead = await _lead_ativo(session, usuario, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    existente = await _pendente_lead(session, usuario, lead.id)
    if existente is not None:
        response.status_code = status.HTTP_200_OK
        return _solicitacao_lead_response(existente)
    item = SolicitacaoExclusaoLead(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead.id,
        lead_nome=(lead.nome or "Lead")[:200],
        solicitado_por_id=usuario.id,
        solicitado_por=usuario.ator,
        motivo=dados.motivo,
        status="pendente",
    )
    session.add(item)
    _auditar(
        session, usuario, request, "solicitar_exclusao", f"lead:{lead.id}", True, 201, {"motivo": dados.motivo}
    )
    try:
        await session.commit()
    except IntegrityError:
        # Pedido simultâneo do mesmo lead: o índice único parcial garante uma só pendente.
        await session.rollback()
        existente = await _pendente_lead(session, usuario, lead_id)
        if existente is None:
            raise
        response.status_code = status.HTTP_200_OK
        return _solicitacao_lead_response(existente)
    await session.refresh(item)
    return _solicitacao_lead_response(item)


@router.get("/v1/admin/exclusoes-leads")
async def listar_solicitacoes_lead(
    session: SessionDep,
    usuario: PesquisaDeleteDep,
    status_solicitacao: str = "pendente",
    limite: Annotated[int, Query(ge=1, le=200)] = 100,
    busca: Annotated[str, Query(max_length=100)] = "",
) -> dict:
    filtros = [SolicitacaoExclusaoLead.organizacao_id == usuario.organizacao_id]
    if status_solicitacao:
        filtros.append(SolicitacaoExclusaoLead.status == status_solicitacao)
    termo = busca.strip()
    if termo:
        padrao = "%" + termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        filtros.append(
            or_(
                SolicitacaoExclusaoLead.lead_nome.ilike(padrao, escape="\\"),
                SolicitacaoExclusaoLead.solicitado_por.ilike(padrao, escape="\\"),
                SolicitacaoExclusaoLead.motivo.ilike(padrao, escape="\\"),
            )
        )
    total = (await session.execute(select(func.count()).select_from(SolicitacaoExclusaoLead).where(*filtros))).scalar_one()
    itens = (
        (
            await session.execute(
                select(SolicitacaoExclusaoLead)
                .where(*filtros)
                .order_by(SolicitacaoExclusaoLead.criado_em.desc(), SolicitacaoExclusaoLead.id.desc())
                .limit(limite)
            )
        )
        .scalars()
        .all()
    )
    return {"total": total, "exibidas": len(itens), "itens": [_solicitacao_lead_response(i) for i in itens]}


async def _decidir_lead(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    request: Request,
    item: SolicitacaoExclusaoLead,
    aprovar: bool,
    observacao: str | None,
    agora: datetime,
    modo: str,
) -> bool:
    """Aplica a decisão. Devolve True quando o lead já não estava ativo (pedido encerrado sem efeito)."""
    lead = await _lead_ativo(session, usuario, item.lead_id) if aprovar else None
    ja_arquivado = aprovar and lead is None
    item.status = "executada" if aprovar else "rejeitada"
    item.decidido_por_id = usuario.id
    item.decidido_por = usuario.ator
    item.decisao_observacao = "O contato já estava arquivado" if ja_arquivado else observacao
    item.decidido_em = agora
    if lead is not None:
        lead.arquivado_em = agora
    _auditar(
        session,
        usuario,
        request,
        "aprovar_exclusao" if aprovar else "rejeitar_exclusao",
        f"lead:{item.lead_id}",
        True,
        200,
        {"solicitacao_id": item.id, "modo": modo, "efeito": "arquivado" if lead is not None else "nenhum"},
    )
    return ja_arquivado


@router.post("/v1/admin/exclusoes-leads/decidir-lote")
async def decidir_solicitacoes_lead_em_lote(
    dados: DecidirLoteInput,
    request: Request,
    session: SessionDep,
    usuario: PesquisaDeleteDep,
    _limite: AcaoAdminDep,
) -> dict:
    ids = list(dict.fromkeys(dados.ids))
    itens = (
        (
            await session.execute(
                select(SolicitacaoExclusaoLead)
                .where(
                    SolicitacaoExclusaoLead.id.in_(ids),
                    SolicitacaoExclusaoLead.organizacao_id == usuario.organizacao_id,
                    SolicitacaoExclusaoLead.status == "pendente",
                )
                .order_by(SolicitacaoExclusaoLead.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    aprovar = dados.decisao == "aprovar"
    if aprovar:
        await _exigir_senha(session, usuario, dados.senha, request, "leads:lote")
    agora = datetime.now(UTC)
    ja = 0
    for item in itens:
        ja += int(await _decidir_lead(session, usuario, request, item, aprovar, dados.observacao, agora, "lote"))
    await session.commit()
    return {
        "decisao": dados.decisao,
        "processadas": len(itens),
        "ja_removidas": ja,
        "ignoradas": len(ids) - len(itens),
    }


@router.post("/v1/admin/exclusoes-leads/{solicitacao_id}/decidir")
async def decidir_solicitacao_lead(
    solicitacao_id: int,
    dados: DecidirExclusaoInput,
    request: Request,
    session: SessionDep,
    usuario: PesquisaDeleteDep,
    _limite: AcaoAdminDep,
) -> dict:
    item = (
        await session.execute(
            select(SolicitacaoExclusaoLead)
            .where(
                SolicitacaoExclusaoLead.id == solicitacao_id,
                SolicitacaoExclusaoLead.organizacao_id == usuario.organizacao_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    if item.status != "pendente":
        raise HTTPException(status_code=409, detail="Solicitação já foi decidida")
    aprovar = dados.decisao == "aprovar"
    if aprovar:
        await _exigir_senha(session, usuario, dados.senha, request, f"lead:{item.lead_id}")
        if await _lead_ativo(session, usuario, item.lead_id) is None:
            raise HTTPException(status_code=409, detail="O contato já não está ativo")
    await _decidir_lead(session, usuario, request, item, aprovar, dados.observacao, datetime.now(UTC), "individual")
    await session.commit()
    return _solicitacao_lead_response(item)

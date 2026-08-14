from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    AcaoAdminDep,
    UsuarioAutenticado,
    exigir_permissao,
    hash_ip,
    verificar_senha,
)
from app.database import get_session
from app.models import (
    EventoAuditoria,
    PesquisaMarca,
    SolicitacaoExclusaoPesquisa,
    UsuarioOperacoes,
)
from app.proxy import cliente_ip

router = APIRouter(tags=["exclusao de pesquisas"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
PesquisaViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
PesquisaDeleteDep = Annotated[
    UsuarioAutenticado, Depends(exigir_permissao("leads.delete"))
]


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


async def _pesquisa(
    session: AsyncSession, usuario: UsuarioAutenticado, pesquisa_id: str
) -> PesquisaMarca:
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
    if not senha or registro is None or not verificar_senha(registro.senha_hash, senha):
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
) -> dict:
    filtros = [SolicitacaoExclusaoPesquisa.organizacao_id == usuario.organizacao_id]
    if status_solicitacao:
        filtros.append(SolicitacaoExclusaoPesquisa.status == status_solicitacao)
    itens = (
        await session.execute(
            select(SolicitacaoExclusaoPesquisa)
            .where(*filtros)
            .order_by(SolicitacaoExclusaoPesquisa.criado_em.desc())
            .limit(100)
        )
    ).scalars().all()
    return {"total": len(itens), "itens": [_solicitacao_response(item) for item in itens]}


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

"""Apontamento de horas fatuaveis -- Fase A da evolucao do CRM (05/09/2026):
achado da auditoria comparando com CRMs juridicos de mercado -- o sistema
so tinha custo monetario (CustoJuridico), sem nenhum controle de horas
trabalhadas por operador/processo/lead."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import ApontamentoHoras, EventoAuditoria, Lead, ProcessoMonitorado
from app.proxy import cliente_ip

router = APIRouter(prefix="/v1/admin/horas", tags=["crm"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]


class ApontamentoInput(BaseModel):
    lead_id: int | None = Field(default=None, ge=1)
    processo_monitorado_id: int | None = Field(default=None, ge=1)
    data: date
    horas: Decimal = Field(gt=0, le=24)
    descricao: str = Field(min_length=3, max_length=500)
    faturavel: bool = True

    @model_validator(mode="after")
    def _exige_vinculo(self) -> "ApontamentoInput":
        if self.lead_id is None and self.processo_monitorado_id is None:
            raise ValueError("Informe ao menos um lead ou processo monitorado")
        return self


def _auditar(session: AsyncSession, request: Request, usuario: UsuarioAutenticado, acao: str, recurso: str, detalhes: dict) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao=acao[:20],
            recurso=recurso[:180],
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes=detalhes,
        )
    )


async def _validar_vinculos(session: AsyncSession, usuario: UsuarioAutenticado, dados: ApontamentoInput) -> None:
    if dados.lead_id is not None:
        existe = (
            await session.execute(
                select(Lead.id).where(Lead.id == dados.lead_id, Lead.organizacao_id == usuario.organizacao_id)
            )
        ).scalar_one_or_none()
        if existe is None:
            raise HTTPException(404, "Lead não encontrado")
    if dados.processo_monitorado_id is not None:
        existe = (
            await session.execute(
                select(ProcessoMonitorado.id).where(
                    ProcessoMonitorado.id == dados.processo_monitorado_id,
                    ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if existe is None:
            raise HTTPException(404, "Processo monitorado não encontrado")


def _serializar(item: ApontamentoHoras, usuario_nome: str | None = None) -> dict:
    return {
        "id": item.id,
        "usuario_id": item.usuario_id,
        "usuario_nome": usuario_nome if usuario_nome is not None else (item.usuario.nome if item.usuario else None),
        "lead_id": item.lead_id,
        "processo_monitorado_id": item.processo_monitorado_id,
        "data": item.data,
        "horas": str(item.horas),
        "descricao": item.descricao,
        "faturavel": item.faturavel,
        "criado_em": item.criado_em,
    }


@router.get("")
async def listar_apontamentos(
    session: SessionDep,
    usuario: ViewDep,
    lead_id: Annotated[int | None, Query()] = None,
    processo_monitorado_id: Annotated[int | None, Query()] = None,
    usuario_id: Annotated[int | None, Query()] = None,
    data_de: Annotated[date | None, Query()] = None,
    data_ate: Annotated[date | None, Query()] = None,
) -> dict:
    filtros = [ApontamentoHoras.organizacao_id == usuario.organizacao_id]
    if lead_id:
        filtros.append(ApontamentoHoras.lead_id == lead_id)
    if processo_monitorado_id:
        filtros.append(ApontamentoHoras.processo_monitorado_id == processo_monitorado_id)
    if usuario_id:
        filtros.append(ApontamentoHoras.usuario_id == usuario_id)
    if data_de:
        filtros.append(ApontamentoHoras.data >= data_de)
    if data_ate:
        filtros.append(ApontamentoHoras.data <= data_ate)
    itens = (
        (
            await session.execute(
                select(ApontamentoHoras).where(*filtros).order_by(ApontamentoHoras.data.desc())
            )
        )
        .scalars()
        .all()
    )
    total_horas = sum((item.horas for item in itens), Decimal("0"))
    total_faturavel = sum((item.horas for item in itens if item.faturavel), Decimal("0"))
    return {
        "itens": [_serializar(item) for item in itens],
        "total_horas": str(total_horas),
        "total_horas_faturaveis": str(total_faturavel),
    }


@router.post("", status_code=201)
async def criar_apontamento(dados: ApontamentoInput, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    await _validar_vinculos(session, usuario, dados)
    item = ApontamentoHoras(
        organizacao_id=usuario.organizacao_id,
        usuario_id=usuario.id,
        lead_id=dados.lead_id,
        processo_monitorado_id=dados.processo_monitorado_id,
        data=dados.data,
        horas=dados.horas,
        descricao=dados.descricao.strip(),
        faturavel=dados.faturavel,
    )
    session.add(item)
    await session.flush()
    _auditar(session, request, usuario, "apontar_horas", f"apontamento-horas:{item.id}", {"horas": str(item.horas)})
    await session.commit()
    return _serializar(item, usuario_nome=usuario.nome)


@router.patch("/{apontamento_id}")
async def editar_apontamento(
    apontamento_id: int, dados: ApontamentoInput, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    item = (
        await session.execute(
            select(ApontamentoHoras).where(
                ApontamentoHoras.id == apontamento_id, ApontamentoHoras.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Apontamento não encontrado")
    if item.usuario_id != usuario.id and not (usuario.superadmin or usuario.perfil == "administrador"):
        raise HTTPException(403, "Só é possível editar apontamentos próprios")
    await _validar_vinculos(session, usuario, dados)
    item.lead_id = dados.lead_id
    item.processo_monitorado_id = dados.processo_monitorado_id
    item.data = dados.data
    item.horas = dados.horas
    item.descricao = dados.descricao.strip()
    item.faturavel = dados.faturavel
    _auditar(session, request, usuario, "editar_horas", f"apontamento-horas:{item.id}", {"horas": str(item.horas)})
    await session.commit()
    return _serializar(item, usuario_nome=item.usuario.nome if item.usuario else None)


@router.delete("/{apontamento_id}", status_code=204)
async def excluir_apontamento(apontamento_id: int, request: Request, session: SessionDep, usuario: ManageDep) -> None:
    item = (
        await session.execute(
            select(ApontamentoHoras).where(
                ApontamentoHoras.id == apontamento_id, ApontamentoHoras.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Apontamento não encontrado")
    if item.usuario_id != usuario.id and not (usuario.superadmin or usuario.perfil == "administrador"):
        raise HTTPException(403, "Só é possível excluir apontamentos próprios")
    _auditar(session, request, usuario, "excluir_horas", f"apontamento-horas:{item.id}", {})
    await session.delete(item)
    await session.commit()

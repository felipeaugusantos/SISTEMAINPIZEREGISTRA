"""Guias do INPI (GRU) por lead.

Extraído de app/api/leads.py (Fase 4 da missão de maturidade técnica,
14/09/2026) como prova de conceito de modularização segura: mesmo
comportamento e mesmas URLs, só o arquivo mudou -- o padrão de arquivo
próprio + router incluído em app/main.py já existe em leads_propostas.py.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.leads import _auditar, _lead_da_org
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import GuiaInpi, Lead, RetribuicaoInpi

router = APIRouter(tags=["leads"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LeadsViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
LeadsManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]


class GuiaInpiInput(BaseModel):
    descricao: str = Field(min_length=2, max_length=200)
    servico: str | None = Field(default=None, max_length=60)
    codigo: str | None = Field(default=None, max_length=10)
    valor: Decimal | None = Field(default=None, ge=0)
    reduzido: bool = False
    numero_gru: str | None = Field(default=None, max_length=60)
    vencimento: date | None = None
    observacoes: str | None = Field(default=None, max_length=2000)


class GuiaStatusInput(BaseModel):
    status: Literal["pendente", "paga", "cancelada"]
    pago_em: date | None = None


def _guia_dict(g: GuiaInpi) -> dict:
    return {
        "id": g.id,
        "servico": g.servico,
        "codigo": g.codigo,
        "descricao": g.descricao,
        "valor": g.valor,
        "reduzido": g.reduzido,
        "numero_gru": g.numero_gru,
        "vencimento": g.vencimento,
        "status": g.status,
        "pago_em": g.pago_em,
        "observacoes": g.observacoes,
        "criado_em": g.criado_em,
    }


@router.get("/v1/admin/leads/{lead_id}/guias")
async def listar_guias_inpi(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    guias = (
        (
            await session.execute(
                select(GuiaInpi)
                .where(
                    GuiaInpi.lead_id == lead_id,
                    GuiaInpi.organizacao_id == usuario.organizacao_id,
                )
                .order_by(GuiaInpi.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    ja = {g.servico for g in guias if g.servico}
    sugeridas = (
        (
            await session.execute(
                select(RetribuicaoInpi)
                .where(
                    RetribuicaoInpi.fase_sugerida == lead.fase,
                    RetribuicaoInpi.ativo.is_(True),
                )
                .order_by(RetribuicaoInpi.ordem, RetribuicaoInpi.descricao)
            )
        )
        .scalars()
        .all()
    )
    return {
        "fase": lead.fase,
        "guias": [_guia_dict(g) for g in guias],
        "sugestoes": [
            {
                "servico": s.servico,
                "codigo": s.codigo,
                "descricao": s.descricao,
                "valor_normal": s.valor_normal,
                "valor_reduzido": s.valor_reduzido,
                "ja_registrada": s.servico in ja,
            }
            for s in sugeridas
        ],
    }


@router.post("/v1/admin/leads/{lead_id}/guias", status_code=status.HTTP_201_CREATED)
async def criar_guia_inpi(
    lead_id: int, dados: GuiaInpiInput, request: Request, session: SessionDep, usuario: LeadsManageDep
) -> dict:
    await _lead_da_org(session, lead_id, usuario.organizacao_id)
    guia = GuiaInpi(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead_id,
        servico=dados.servico or None,
        codigo=dados.codigo or None,
        descricao=dados.descricao.strip(),
        valor=dados.valor,
        reduzido=dados.reduzido,
        numero_gru=dados.numero_gru or None,
        vencimento=dados.vencimento,
        observacoes=dados.observacoes,
    )
    session.add(guia)
    await session.flush()
    # Achado da Fase 15.1 (auditoria fina de Leads, 23/09/2026): as 3
    # mutações de GuiaInpi (guia de pagamento ao INPI, entidade financeira
    # sensível) não deixavam nenhum rastro de auditoria -- diferente do
    # resto do módulo de leads, que audita quase toda mutação relevante.
    _auditar(
        session, usuario, request, "criar_guia", f"lead:{lead_id}:guia:{guia.id}", {"descricao": guia.descricao}
    )
    await session.commit()
    return {"id": guia.id}


@router.patch("/v1/admin/guias-inpi/{guia_id}")
async def atualizar_guia_inpi(
    guia_id: int, dados: GuiaStatusInput, request: Request, session: SessionDep, usuario: LeadsManageDep
) -> dict:
    guia = (
        await session.execute(
            select(GuiaInpi).where(GuiaInpi.id == guia_id, GuiaInpi.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if guia is None:
        raise HTTPException(status_code=404, detail="Guia não encontrada")
    guia.status = dados.status
    guia.pago_em = (dados.pago_em or datetime.now(UTC).date()) if dados.status == "paga" else None
    _auditar(
        session,
        usuario,
        request,
        "atualizar_guia",
        f"lead:{guia.lead_id}:guia:{guia.id}",
        {"status": dados.status, "pago_em": str(guia.pago_em) if guia.pago_em else None},
    )
    await session.commit()
    return {"ok": True}


@router.delete("/v1/admin/guias-inpi/{guia_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_guia_inpi(guia_id: int, request: Request, session: SessionDep, usuario: LeadsManageDep) -> Response:
    guia = (
        await session.execute(
            select(GuiaInpi).where(GuiaInpi.id == guia_id, GuiaInpi.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if guia is None:
        raise HTTPException(status_code=404, detail="Guia não encontrada")
    _auditar(
        session,
        usuario,
        request,
        "remover_guia",
        f"lead:{guia.lead_id}:guia:{guia.id}",
        {"descricao": guia.descricao},
    )
    await session.delete(guia)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

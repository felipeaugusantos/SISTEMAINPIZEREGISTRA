"""API versionada para departamentos jurídicos e custos operacionais."""

import csv
import hashlib
import hmac
import io
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import (
    CentroCustoFinanceiro,
    ContratoJuridico,
    CustoJuridico,
    DepartamentoFinanceiro,
    FornecedorJuridico,
    WebhookFinanceiro,
)
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin/escritorio", tags=["escritorio juridico"])
webhook_router = APIRouter(prefix="/v1/webhooks/escritorio", tags=["webhooks escritorio"])
SessionDep = Annotated[object, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.manage"))]


class DepartamentoInput(BaseModel):
    codigo: str = Field(min_length=2, max_length=50)
    nome: str = Field(min_length=2, max_length=150)


class CentroCustoInput(DepartamentoInput):
    departamento_id: int | None = None


class FornecedorInput(BaseModel):
    nome: str = Field(min_length=2, max_length=180)
    documento: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=254)


class ContratoInput(BaseModel):
    titulo: str = Field(min_length=2, max_length=180)
    fornecedor_id: int | None = None
    processo_id: int | None = None
    status: str = Field(default="ativo", max_length=30)
    vigencia_inicio: str | None = None
    vigencia_fim: str | None = None
    valor: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    documento_hash: str | None = Field(default=None, min_length=64, max_length=64)


class CustoInput(BaseModel):
    categoria: str = Field(pattern=r"^(custas_inpi|honorarios|despesa|fornecedor|outro)$")
    descricao: str = Field(min_length=2, max_length=240)
    valor: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    departamento_id: int | None = None
    centro_custo_id: int | None = None
    fornecedor_id: int | None = None
    contrato_id: int | None = None
    processo_id: int | None = None
    responsaveis: list[int] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(min_length=8, max_length=120)


class WebhookInput(BaseModel):
    organizacao_id: int
    referencia: str = Field(min_length=2, max_length=150)
    evento: str = Field(min_length=2, max_length=80)
    payload: dict = Field(default_factory=dict)


def _admin(usuario: UsuarioAutenticado) -> bool:
    return usuario.superadmin or usuario.perfil in {"administrador", "ceo", "tech"}


async def _departamento_ok(
    session, usuario: UsuarioAutenticado, departamento_id: int | None
) -> None:
    if departamento_id is None or _admin(usuario) or not getattr(usuario, "departamento", None):
        return
    item = (
        await session.execute(
            select(DepartamentoFinanceiro).where(
                DepartamentoFinanceiro.id == departamento_id,
                DepartamentoFinanceiro.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None or item.codigo != usuario.departamento:
        raise HTTPException(status_code=403, detail="Departamento nao autorizado")


@router.get("/departamentos")
async def listar_departamentos(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (
            await session.execute(
                select(DepartamentoFinanceiro)
                .where(DepartamentoFinanceiro.organizacao_id == usuario.organizacao_id)
                .order_by(DepartamentoFinanceiro.nome)
            )
        )
        .scalars()
        .all()
    )
    if not _admin(usuario) and getattr(usuario, "departamento", None):
        itens = [item for item in itens if item.codigo == usuario.departamento]
    return {
        "itens": [{"id": i.id, "codigo": i.codigo, "nome": i.nome, "ativo": i.ativo} for i in itens]
    }


@router.post("/departamentos", status_code=status.HTTP_201_CREATED)
async def criar_departamento(
    dados: DepartamentoInput, session: SessionDep, usuario: ManageDep
) -> dict:
    item = DepartamentoFinanceiro(organizacao_id=usuario.organizacao_id, **dados.model_dump())
    session.add(item)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Codigo de departamento ja cadastrado"
        ) from None
    return {"id": item.id, "codigo": item.codigo, "nome": item.nome}


@router.get("/centros-custo")
async def listar_centros(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (
            await session.execute(
                select(CentroCustoFinanceiro)
                .where(CentroCustoFinanceiro.organizacao_id == usuario.organizacao_id)
                .order_by(CentroCustoFinanceiro.codigo)
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": i.id,
                "codigo": i.codigo,
                "nome": i.nome,
                "departamento_id": i.departamento_id,
                "ativo": i.ativo,
            }
            for i in itens
        ]
    }


@router.post("/centros-custo", status_code=status.HTTP_201_CREATED)
async def criar_centro(dados: CentroCustoInput, session: SessionDep, usuario: ManageDep) -> dict:
    await _departamento_ok(session, usuario, dados.departamento_id)
    item = CentroCustoFinanceiro(organizacao_id=usuario.organizacao_id, **dados.model_dump())
    session.add(item)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Codigo de centro de custo ja cadastrado"
        ) from None
    return {"id": item.id, "codigo": item.codigo, "nome": item.nome}


@router.get("/fornecedores")
async def listar_fornecedores(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (
            await session.execute(
                select(FornecedorJuridico)
                .where(FornecedorJuridico.organizacao_id == usuario.organizacao_id)
                .order_by(FornecedorJuridico.nome)
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": i.id,
                "nome": i.nome,
                "documento": i.documento,
                "email": i.email,
                "ativo": i.ativo,
            }
            for i in itens
        ]
    }


@router.post("/fornecedores", status_code=status.HTTP_201_CREATED)
async def criar_fornecedor(dados: FornecedorInput, session: SessionDep, usuario: ManageDep) -> dict:
    item = FornecedorJuridico(organizacao_id=usuario.organizacao_id, **dados.model_dump())
    session.add(item)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Fornecedor ja cadastrado") from None
    return {"id": item.id, "nome": item.nome}


@router.get("/contratos")
async def listar_contratos(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (
            await session.execute(
                select(ContratoJuridico)
                .where(ContratoJuridico.organizacao_id == usuario.organizacao_id)
                .order_by(ContratoJuridico.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": i.id,
                "titulo": i.titulo,
                "fornecedor_id": i.fornecedor_id,
                "processo_id": i.processo_id,
                "status": i.status,
                "vigencia_fim": i.vigencia_fim,
            }
            for i in itens
        ]
    }


@router.post("/contratos", status_code=status.HTTP_201_CREATED)
async def criar_contrato(dados: ContratoInput, session: SessionDep, usuario: ManageDep) -> dict:
    from datetime import date

    valores = dados.model_dump()
    for campo in ("vigencia_inicio", "vigencia_fim"):
        if valores[campo]:
            valores[campo] = date.fromisoformat(valores[campo])
    item = ContratoJuridico(organizacao_id=usuario.organizacao_id, **valores)
    session.add(item)
    await session.commit()
    return {"id": item.id, "titulo": item.titulo, "status": item.status}


@router.post("/custos", status_code=status.HTTP_201_CREATED)
async def criar_custo(dados: CustoInput, session: SessionDep, usuario: ManageDep) -> dict:
    await _departamento_ok(session, usuario, dados.departamento_id)
    existente = (
        await session.execute(
            select(CustoJuridico).where(
                CustoJuridico.organizacao_id == usuario.organizacao_id,
                CustoJuridico.idempotency_key == dados.idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existente:
        return {"id": existente.id, "idempotente": True, "valor": str(existente.valor)}
    item = CustoJuridico(
        organizacao_id=usuario.organizacao_id, criado_por_id=usuario.id, **dados.model_dump()
    )
    session.add(item)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existente = (
            await session.execute(
                select(CustoJuridico).where(
                    CustoJuridico.organizacao_id == usuario.organizacao_id,
                    CustoJuridico.idempotency_key == dados.idempotency_key,
                )
            )
        ).scalar_one()
        return {"id": existente.id, "idempotente": True, "valor": str(existente.valor)}
    return {"id": item.id, "idempotente": False, "valor": str(item.valor)}


@router.get("/custos")
async def listar_custos(
    session: SessionDep, usuario: ViewDep, departamento_id: int | None = None
) -> dict:
    await _departamento_ok(session, usuario, departamento_id)
    stmt = select(CustoJuridico).where(CustoJuridico.organizacao_id == usuario.organizacao_id)
    if departamento_id is not None:
        stmt = stmt.where(CustoJuridico.departamento_id == departamento_id)
    itens = (
        (await session.execute(stmt.order_by(CustoJuridico.criado_em.desc()).limit(500)))
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": i.id,
                "categoria": i.categoria,
                "descricao": i.descricao,
                "valor": str(i.valor),
                "departamento_id": i.departamento_id,
                "responsaveis": i.responsaveis,
            }
            for i in itens
        ]
    }


@router.get("/custos/exportar.csv")
async def exportar_custos(session: SessionDep, usuario: ViewDep) -> Response:
    itens = (
        (
            await session.execute(
                select(CustoJuridico)
                .where(CustoJuridico.organizacao_id == usuario.organizacao_id)
                .order_by(CustoJuridico.criado_em)
            )
        )
        .scalars()
        .all()
    )
    saida = io.StringIO()
    escritor = csv.writer(saida)
    escritor.writerow(["id", "categoria", "descricao", "valor", "departamento_id"])
    for item in itens:
        escritor.writerow(
            [item.id, item.categoria, item.descricao, item.valor, item.departamento_id]
        )
    return Response(
        saida.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="custos-juridicos.csv"'},
    )


@router.get("/relatorio-executivo")
async def relatorio_executivo(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (
            await session.execute(
                select(CustoJuridico).where(CustoJuridico.organizacao_id == usuario.organizacao_id)
            )
        )
        .scalars()
        .all()
    )
    por_categoria: dict[str, Decimal] = {}
    for item in itens:
        por_categoria[item.categoria] = por_categoria.get(item.categoria, Decimal("0")) + item.valor
    return {
        "total": str(sum(por_categoria.values(), Decimal("0"))),
        "quantidade": len(itens),
        "por_categoria": {chave: str(valor) for chave, valor in por_categoria.items()},
    }


@router.post("/webhooks/{webhook_id}/reprocessar")
async def reprocessar_webhook(webhook_id: int, session: SessionDep, usuario: ManageDep) -> dict:
    evento = (
        await session.execute(
            select(WebhookFinanceiro).where(
                WebhookFinanceiro.id == webhook_id,
                WebhookFinanceiro.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if evento is None:
        raise HTTPException(status_code=404, detail="Webhook nao encontrado")
    evento.tentativas += 1
    evento.status = "reprocessado"
    await session.commit()
    return {"id": evento.id, "status": evento.status, "tentativas": evento.tentativas}


@webhook_router.post("/financeiro")
async def receber_webhook(
    dados: WebhookInput,
    request: Request,
    session: SessionDep,
    x_signature: str | None = Header(default=None),
) -> dict:
    segredo = get_settings().gateway_webhook_secret
    corpo = await request.body()
    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest() if segredo else ""
    if not segredo or not x_signature or not hmac.compare_digest(x_signature, esperado):
        raise HTTPException(status_code=401, detail="Assinatura do webhook invalida")
    existente = (
        await session.execute(
            select(WebhookFinanceiro).where(
                WebhookFinanceiro.organizacao_id == dados.organizacao_id,
                WebhookFinanceiro.referencia == dados.referencia,
            )
        )
    ).scalar_one_or_none()
    if existente:
        existente.tentativas += 1
        await session.commit()
        return {"id": existente.id, "idempotente": True, "status": existente.status}
    evento = WebhookFinanceiro(
        organizacao_id=dados.organizacao_id,
        referencia=dados.referencia,
        evento=dados.evento,
        payload=dados.payload,
    )
    session.add(evento)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"idempotente": True}
    return {"id": evento.id, "idempotente": False, "status": evento.status}

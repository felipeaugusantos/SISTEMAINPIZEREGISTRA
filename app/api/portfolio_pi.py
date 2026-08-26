import base64
import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal_cliente import ClientDep
from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import (
    AtivoPartePI,
    AtivoPI,
    AtivoProcessoPI,
    ClientePortal,
    DocumentoAtivoPI,
    EventoAuditoria,
    Processo,
    TipoAtivoPI,
    Titular,
)

router = APIRouter(prefix="/v1/admin/portfolio", tags=["portfolio de propriedade intelectual"])
portal_router = APIRouter(tags=["portal-cliente"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("legal.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("legal.manage"))]

TIPOS = {item.value for item in TipoAtivoPI}
PAPEIS = {"inventor", "procurador", "titular_adicional"}


class AtivoInput(BaseModel):
    codigo: str = Field(min_length=1, max_length=80)
    tipo: Literal[
        "marca",
        "patente",
        "modelo_utilidade",
        "desenho_industrial",
        "contrato",
        "cessao",
        "licenca",
        "franquia",
    ]
    nome: str = Field(min_length=2, max_length=240)
    titular_id: int = Field(ge=1)
    cliente_portal_id: int | None = Field(default=None, ge=1)
    status: str = Field(default="ativo", max_length=30)
    vigencia_inicio: date | None = None
    vigencia_fim: date | None = None
    dados: dict = Field(default_factory=dict)


class ParteInput(BaseModel):
    papel: Literal["inventor", "procurador", "titular_adicional"]
    nome: str = Field(min_length=2, max_length=240)
    documento: str | None = Field(default=None, max_length=30)


class ProcessoInput(BaseModel):
    processo_id: int = Field(ge=1)
    papel: str = Field(default="principal", max_length=30)


class DocumentoInput(BaseModel):
    nome: str = Field(min_length=1, max_length=255)
    conteudo_base64: str = Field(min_length=1)
    content_type: str | None = Field(default=None, max_length=120)


def _slug(valor: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9._-]", "_", valor))[:180] or "documento"


async def _obter_ativo(session: AsyncSession, usuario: UsuarioAutenticado, ativo_id: int) -> AtivoPI:
    ativo = (
        await session.execute(
            select(AtivoPI).where(AtivoPI.id == ativo_id, AtivoPI.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if ativo is None:
        raise HTTPException(status_code=404, detail="Ativo de propriedade intelectual não encontrado")
    return ativo


def _auditar(
    session: AsyncSession,
    request: Request,
    usuario: UsuarioAutenticado,
    acao: str,
    recurso: str,
    detalhes: dict,
) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao=acao,
            recurso=recurso,
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(request.client.host if request.client else None),
            detalhes=detalhes,
        )
    )


def _serializar(ativo: AtivoPI, titular: Titular | None = None) -> dict:
    return {
        "id": ativo.id,
        "codigo": ativo.codigo,
        "tipo": ativo.tipo,
        "nome": ativo.nome,
        "status": ativo.status,
        "titular": {"id": ativo.titular_id, "nome": titular.nome if titular else None},
        "cliente_portal_id": ativo.cliente_portal_id,
        "vigencia_inicio": ativo.vigencia_inicio,
        "vigencia_fim": ativo.vigencia_fim,
        "dados": ativo.dados,
        "documentos_url": f"/v1/admin/portfolio/ativos/{ativo.id}/documentos",
        "processos_url": f"/v1/admin/portfolio/ativos/{ativo.id}/processos",
    }


@router.get("/ativos")
async def listar_ativos(
    session: SessionDep,
    usuario: ViewDep,
    tipo: str | None = None,
    status: str | None = None,
    cliente_portal_id: int | None = None,
    busca: str | None = None,
) -> dict:
    filtros = [AtivoPI.organizacao_id == usuario.organizacao_id]
    if tipo:
        if tipo not in TIPOS:
            raise HTTPException(status_code=422, detail="Tipo de ativo inválido")
        filtros.append(AtivoPI.tipo == tipo)
    if status:
        filtros.append(AtivoPI.status == status)
    if cliente_portal_id:
        filtros.append(AtivoPI.cliente_portal_id == cliente_portal_id)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(AtivoPI.nome.ilike(termo) | AtivoPI.codigo.ilike(termo))
    linhas = (
        await session.execute(
            select(AtivoPI, Titular)
            .join(Titular, Titular.id == AtivoPI.titular_id)
            .where(*filtros)
            .order_by(AtivoPI.nome)
        )
    ).all()
    return {
        "ativos": [_serializar(ativo, titular) for ativo, titular in linhas],
        "tipos": sorted(TIPOS),
    }


@router.post("/ativos", status_code=201)
async def criar_ativo(dados: AtivoInput, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    titular = (await session.execute(select(Titular).where(Titular.id == dados.titular_id))).scalar_one_or_none()
    if titular is None:
        raise HTTPException(status_code=422, detail="Titular não encontrado")
    if dados.cliente_portal_id:
        cliente = (
            await session.execute(
                select(ClientePortal).where(
                    ClientePortal.id == dados.cliente_portal_id,
                    ClientePortal.organizacao_id == usuario.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente do portal não encontrado")
    ativo = AtivoPI(organizacao_id=usuario.organizacao_id, criado_por=usuario.ator, **dados.model_dump())
    session.add(ativo)
    await session.flush()
    _auditar(
        session,
        request,
        usuario,
        "criar_ativo_pi",
        f"ativo:{ativo.id}",
        {"tipo": ativo.tipo, "codigo": ativo.codigo},
    )
    await session.commit()
    return {"ativo": _serializar(ativo, titular)}


@router.post("/ativos/{ativo_id}/processos", status_code=201)
async def vincular_processo(
    ativo_id: int, dados: ProcessoInput, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    ativo = await _obter_ativo(session, usuario, ativo_id)
    processo = (await session.execute(select(Processo).where(Processo.id == dados.processo_id))).scalar_one_or_none()
    if processo is None:
        raise HTTPException(status_code=404, detail="Processo não encontrado")
    if ativo.tipo in {"marca"} and processo.tipo.value != "marca":
        raise HTTPException(status_code=422, detail="Processo incompatível com o ativo de marca")
    if ativo.tipo in {"patente", "modelo_utilidade"} and processo.tipo.value != "patente":
        raise HTTPException(status_code=422, detail="Processo incompatível com o ativo técnico")
    link = AtivoProcessoPI(
        organizacao_id=usuario.organizacao_id,
        ativo_id=ativo.id,
        processo_id=processo.id,
        papel=dados.papel,
    )
    session.add(link)
    _auditar(
        session,
        request,
        usuario,
        "vincular_processo_ativo_pi",
        f"ativo:{ativo.id}",
        {"processo_id": processo.id, "papel": dados.papel},
    )
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Processo já vinculado a este ativo") from exc
    return {"id": link.id, "ativo_id": ativo.id, "processo_id": processo.id, "papel": link.papel}


@router.get("/ativos/{ativo_id}/processos")
async def listar_processos(ativo_id: int, session: SessionDep, usuario: ViewDep) -> dict:
    await _obter_ativo(session, usuario, ativo_id)
    linhas = (
        await session.execute(
            select(AtivoProcessoPI, Processo)
            .join(Processo, Processo.id == AtivoProcessoPI.processo_id)
            .where(
                AtivoProcessoPI.ativo_id == ativo_id,
                AtivoProcessoPI.organizacao_id == usuario.organizacao_id,
            )
            .order_by(Processo.numero)
        )
    ).all()
    return {
        "processos": [
            {
                "id": link.processo_id,
                "numero": processo.numero,
                "titulo": processo.titulo,
                "tipo": processo.tipo.value,
                "papel": link.papel,
            }
            for link, processo in linhas
        ]
    }


@router.post("/ativos/{ativo_id}/partes", status_code=201)
async def adicionar_parte(
    ativo_id: int, dados: ParteInput, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    ativo = await _obter_ativo(session, usuario, ativo_id)
    parte = AtivoPartePI(organizacao_id=usuario.organizacao_id, ativo_id=ativo.id, **dados.model_dump())
    session.add(parte)
    _auditar(
        session,
        request,
        usuario,
        "adicionar_parte_ativo_pi",
        f"ativo:{ativo.id}",
        dados.model_dump(),
    )
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Parte já cadastrada neste ativo") from exc
    return {"id": parte.id, **dados.model_dump()}


@router.post("/ativos/{ativo_id}/documentos", status_code=201)
async def adicionar_documento(
    ativo_id: int, dados: DocumentoInput, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    ativo = await _obter_ativo(session, usuario, ativo_id)
    try:
        conteudo = base64.b64decode(dados.conteudo_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="conteúdo base64 inválido") from exc
    digest = hashlib.sha256(conteudo).hexdigest()
    ultima = (
        await session.execute(select(func.max(DocumentoAtivoPI.versao)).where(DocumentoAtivoPI.ativo_id == ativo.id))
    ).scalar_one() or 0
    versao = int(ultima) + 1
    pasta = Path("data") / "portfolio_pi" / str(usuario.organizacao_id) / str(ativo.id)
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{versao}-{_slug(dados.nome)}"
    caminho.write_bytes(conteudo)
    documento = DocumentoAtivoPI(
        organizacao_id=usuario.organizacao_id,
        ativo_id=ativo.id,
        nome=dados.nome,
        versao=versao,
        hash_documento=digest,
        caminho=str(caminho),
        content_type=dados.content_type,
        criado_por=usuario.ator,
    )
    session.add(documento)
    _auditar(
        session,
        request,
        usuario,
        "criar_versao_documento_ativo_pi",
        f"ativo:{ativo.id}",
        {"documento": dados.nome, "versao": versao, "hash": digest},
    )
    await session.commit()
    return {"id": documento.id, "ativo_id": ativo.id, "versao": versao, "hash": digest}


@router.get("/ativos/{ativo_id}/documentos")
async def listar_documentos(ativo_id: int, session: SessionDep, usuario: ViewDep) -> dict:
    await _obter_ativo(session, usuario, ativo_id)
    itens = (
        (
            await session.execute(
                select(DocumentoAtivoPI)
                .where(
                    DocumentoAtivoPI.ativo_id == ativo_id,
                    DocumentoAtivoPI.organizacao_id == usuario.organizacao_id,
                )
                .order_by(DocumentoAtivoPI.versao.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "documentos": [
            {
                "id": item.id,
                "nome": item.nome,
                "versao": item.versao,
                "hash": item.hash_documento,
                "content_type": item.content_type,
                "criado_em": item.criado_em,
            }
            for item in itens
        ]
    }


@portal_router.get("/v1/portal/ativos", include_in_schema=True)
async def listar_ativos_portal(cliente: ClientDep, session: SessionDep) -> dict:
    linhas = (
        await session.execute(
            select(AtivoPI, Titular)
            .join(Titular, Titular.id == AtivoPI.titular_id)
            .where(
                AtivoPI.organizacao_id == cliente.organizacao_id,
                AtivoPI.cliente_portal_id == cliente.id,
                AtivoPI.status != "arquivado",
            )
            .order_by(AtivoPI.nome)
        )
    ).all()
    return {"ativos": [_serializar(ativo, titular) for ativo, titular in linhas]}

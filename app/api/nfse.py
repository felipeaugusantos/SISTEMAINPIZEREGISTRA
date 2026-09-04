"""Emissão de NFS-e -- achado FASE7-6 da auditoria (04/09/2026): não existia
nenhuma emissão de nota fiscal, só ReciboFinanceiro (recibo interno, sem
valor fiscal). Sempre uma ação explícita do operador para um lançamento de
receita específico -- nunca automático."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import EmpresaCRM, EventoAuditoria, LancamentoFinanceiro, NotaFiscalServico
from app.nfse import AdaptadorNFSeIndisponivelError, obter_adaptador_nfse
from app.proxy import cliente_ip

router = APIRouter(prefix="/v1/admin/financeiro/nfse", tags=["financeiro"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.manage"))]


class EmitirNfseInput(BaseModel):
    lancamento_id: int = Field(ge=1)
    adaptador: str = "sandbox"


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


@router.get("")
async def listar_nfse(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (
            await session.execute(
                select(NotaFiscalServico)
                .where(NotaFiscalServico.organizacao_id == usuario.organizacao_id)
                .order_by(NotaFiscalServico.emitida_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": n.id,
                "lancamento_id": n.lancamento_id,
                "numero": n.numero,
                "codigo_verificacao": n.codigo_verificacao,
                "valor": str(n.valor),
                "status": n.status,
                "emitida_em": n.emitida_em,
            }
            for n in itens
        ]
    }


@router.post("/emitir", status_code=201)
async def emitir_nfse(dados: EmitirNfseInput, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    lancamento = (
        await session.execute(
            select(LancamentoFinanceiro).where(
                LancamentoFinanceiro.id == dados.lancamento_id,
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if lancamento is None:
        raise HTTPException(404, "Lançamento não encontrado")
    if lancamento.tipo != "receber":
        raise HTTPException(422, "Só lançamentos de receita (tipo=receber) podem gerar NFS-e")
    if lancamento.status == "cancelado":
        raise HTTPException(422, "Lançamento cancelado não pode gerar NFS-e")

    empresa = await session.get(EmpresaCRM, lancamento.empresa_id) if lancamento.empresa_id else None
    if empresa is None or not empresa.documento:
        raise HTTPException(
            422,
            "Lançamento sem cliente com CPF/CNPJ cadastrado -- não é possível emitir NFS-e sem o "
            "documento do tomador. Cadastre o documento da empresa/cliente antes de tentar de novo.",
        )

    try:
        adaptador_obj = obter_adaptador_nfse(dados.adaptador)
    except AdaptadorNFSeIndisponivelError as exc:
        raise HTTPException(404, str(exc)) from exc

    try:
        emitida = await adaptador_obj.emitir(
            valor=Decimal(lancamento.valor_total),
            descricao_servico=lancamento.descricao,
            tomador_documento=empresa.documento,
            tomador_nome=empresa.nome,
            competencia=lancamento.competencia,
        )
    except Exception as exc:  # noqa: BLE001 - erro de um adaptador externo, registrado como tentativa falha
        nota_erro = NotaFiscalServico(
            organizacao_id=usuario.organizacao_id,
            lancamento_id=lancamento.id,
            adaptador=dados.adaptador,
            valor=lancamento.valor_total,
            status="erro",
            erro_detalhe=str(exc)[:2000],
            emitida_por=usuario.ator,
        )
        session.add(nota_erro)
        await session.commit()
        raise HTTPException(502, f"Falha ao emitir NFS-e: {exc}") from exc

    nota = NotaFiscalServico(
        organizacao_id=usuario.organizacao_id,
        lancamento_id=lancamento.id,
        adaptador=dados.adaptador,
        numero=emitida.numero,
        codigo_verificacao=emitida.codigo_verificacao,
        valor=lancamento.valor_total,
        status="emitida",
        emitida_por=usuario.ator,
    )
    session.add(nota)
    await session.flush()
    _auditar(session, request, usuario, "emitir_nfse", f"nfse:{nota.id}", {"numero": nota.numero})
    await session.commit()
    return {"id": nota.id, "numero": nota.numero, "codigo_verificacao": nota.codigo_verificacao}


@router.post("/{nota_id}/cancelar")
async def cancelar_nfse(nota_id: int, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    nota = (
        await session.execute(
            select(NotaFiscalServico).where(
                NotaFiscalServico.id == nota_id, NotaFiscalServico.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if nota is None:
        raise HTTPException(404, "NFS-e não encontrada")
    if nota.status != "emitida":
        raise HTTPException(409, f'NFS-e está "{nota.status}", não pode ser cancelada')

    try:
        adaptador_obj = obter_adaptador_nfse(nota.adaptador)
    except AdaptadorNFSeIndisponivelError as exc:
        raise HTTPException(404, str(exc)) from exc
    await adaptador_obj.cancelar(nota.numero)

    nota.status = "cancelada"
    nota.cancelada_em = datetime.now(UTC)
    nota.cancelada_por = usuario.ator
    _auditar(session, request, usuario, "cancelar_nfse", f"nfse:{nota.id}", {})
    await session.commit()
    return {"status": "cancelada"}

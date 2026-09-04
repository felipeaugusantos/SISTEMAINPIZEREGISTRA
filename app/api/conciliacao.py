"""Conciliação bancária -- achado FASE7-3 da auditoria (04/09/2026): não
existia nenhum jeito de confrontar o extrato do banco contra os
lançamentos financeiros internos.

Fluxo: importa um extrato OFX (app/ofx.py) -> cada transação de crédito é
casada automaticamente com uma ParcelaFinanceira já paga (mesmo valor,
data de pagamento numa janela de ±3 dias, ainda não conciliada com outra
transação) -- só concilia sozinho quando há exatamente UMA correspondência;
ambíguo ou sem correspondência fica "pendente" para revisão manual.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import EventoAuditoria, ExtratoBancario, ParcelaFinanceira, TransacaoBancaria
from app.ofx import OfxInvalidoError, parsear_ofx
from app.proxy import cliente_ip

router = APIRouter(prefix="/v1/admin/financeiro/conciliacao", tags=["financeiro"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.manage"))]

TAMANHO_MAXIMO_OFX = 5_000_000
JANELA_DIAS_CONCILIACAO = 3


class ConciliarManualInput(BaseModel):
    parcela_id: int = Field(ge=1)


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


async def _tentar_conciliar_automaticamente(
    session: AsyncSession, organizacao_id: int, transacao: TransacaoBancaria
) -> None:
    if transacao.tipo != "credito":
        return
    ja_vinculadas = set(
        (
            await session.execute(
                select(TransacaoBancaria.parcela_id).where(
                    TransacaoBancaria.organizacao_id == organizacao_id,
                    TransacaoBancaria.parcela_id.is_not(None),
                    TransacaoBancaria.id != transacao.id,
                )
            )
        )
        .scalars()
        .all()
    )
    candidatas = (
        await session.execute(
            select(ParcelaFinanceira.id).where(
                ParcelaFinanceira.organizacao_id == organizacao_id,
                ParcelaFinanceira.status == "paga",
                ParcelaFinanceira.valor_pago == transacao.valor,
                ParcelaFinanceira.pago_em >= transacao.data - timedelta(days=JANELA_DIAS_CONCILIACAO),
                ParcelaFinanceira.pago_em <= transacao.data + timedelta(days=JANELA_DIAS_CONCILIACAO),
            )
        )
    ).scalars().all()
    disponiveis = [pid for pid in candidatas if pid not in ja_vinculadas]
    if len(disponiveis) == 1:
        transacao.parcela_id = disponiveis[0]
        transacao.status = "conciliada"
        transacao.conciliado_em = datetime.now(UTC)
        transacao.conciliado_por = "sistema (automático)"


@router.post("/importar", status_code=201)
async def importar_extrato(request: Request, session: SessionDep, usuario: ManageDep, arquivo: UploadFile) -> dict:
    conteudo = await arquivo.read()
    if len(conteudo) > TAMANHO_MAXIMO_OFX:
        raise HTTPException(413, "Arquivo maior que 5 MB.")
    try:
        texto = conteudo.decode("utf-8", errors="ignore")
        transacoes_ofx = parsear_ofx(texto)
    except OfxInvalidoError as exc:
        raise HTTPException(422, str(exc)) from exc

    extrato = ExtratoBancario(
        organizacao_id=usuario.organizacao_id,
        nome_arquivo=arquivo.filename or "extrato.ofx",
        importado_por=usuario.ator,
    )
    session.add(extrato)
    await session.flush()

    fitids_existentes = set(
        (
            await session.execute(
                select(TransacaoBancaria.fitid).where(TransacaoBancaria.organizacao_id == usuario.organizacao_id)
            )
        )
        .scalars()
        .all()
    )
    importadas, duplicadas, conciliadas = 0, 0, 0
    for item in transacoes_ofx:
        if item.fitid in fitids_existentes:
            duplicadas += 1
            continue
        transacao = TransacaoBancaria(
            organizacao_id=usuario.organizacao_id,
            extrato_id=extrato.id,
            fitid=item.fitid,
            data=item.data,
            valor=abs(item.valor),
            tipo=item.tipo,
            descricao=item.descricao,
        )
        session.add(transacao)
        await session.flush()
        await _tentar_conciliar_automaticamente(session, usuario.organizacao_id, transacao)
        if transacao.status == "conciliada":
            conciliadas += 1
        importadas += 1
        fitids_existentes.add(item.fitid)

    extrato.total_transacoes = importadas
    resultado = {"extrato_id": extrato.id, "importadas": importadas, "duplicadas": duplicadas, "conciliadas_automaticamente": conciliadas}
    _auditar(session, request, usuario, "importar_extrato", f"extrato-bancario:{extrato.id}", resultado)
    await session.commit()
    return resultado


@router.get("")
async def listar_transacoes(
    session: SessionDep,
    usuario: ViewDep,
    status_transacao: Annotated[str | None, Query(alias="status")] = None,
) -> dict:
    filtros = [TransacaoBancaria.organizacao_id == usuario.organizacao_id]
    if status_transacao:
        filtros.append(TransacaoBancaria.status == status_transacao)
    itens = (
        (
            await session.execute(
                select(TransacaoBancaria).where(*filtros).order_by(TransacaoBancaria.data.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": t.id,
                "data": t.data,
                "valor": str(t.valor),
                "tipo": t.tipo,
                "descricao": t.descricao,
                "status": t.status,
                "parcela_id": t.parcela_id,
            }
            for t in itens
        ]
    }


@router.post("/{transacao_id}/conciliar")
async def conciliar_manualmente(
    transacao_id: int, dados: ConciliarManualInput, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    transacao = (
        await session.execute(
            select(TransacaoBancaria).where(
                TransacaoBancaria.id == transacao_id, TransacaoBancaria.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if transacao is None:
        raise HTTPException(404, "Transação não encontrada")
    if transacao.status == "conciliada":
        raise HTTPException(409, "Transação já está conciliada")
    parcela = (
        await session.execute(
            select(ParcelaFinanceira).where(
                ParcelaFinanceira.id == dados.parcela_id, ParcelaFinanceira.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if parcela is None:
        raise HTTPException(404, "Parcela não encontrada")
    if Decimal(parcela.valor_pago or 0) != Decimal(transacao.valor):
        raise HTTPException(
            422,
            f"Valor da parcela ({parcela.valor_pago}) diverge do valor da transação ({transacao.valor}) -- "
            "confira antes de forçar a conciliação manual.",
        )
    transacao.parcela_id = parcela.id
    transacao.status = "conciliada"
    transacao.conciliado_em = datetime.now(UTC)
    transacao.conciliado_por = usuario.ator
    _auditar(
        session, request, usuario, "conciliar_manual", f"transacao-bancaria:{transacao.id}",
        {"parcela_id": parcela.id},
    )
    await session.commit()
    return {"status": "conciliada"}


@router.post("/{transacao_id}/ignorar")
async def ignorar_transacao(transacao_id: int, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    transacao = (
        await session.execute(
            select(TransacaoBancaria).where(
                TransacaoBancaria.id == transacao_id, TransacaoBancaria.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if transacao is None:
        raise HTTPException(404, "Transação não encontrada")
    if transacao.status == "conciliada":
        raise HTTPException(409, "Transação já está conciliada -- desconcilie antes de ignorar, se necessário")
    transacao.status = "ignorada"
    _auditar(session, request, usuario, "ignorar_transacao", f"transacao-bancaria:{transacao.id}", {})
    await session.commit()
    return {"status": "ignorada"}

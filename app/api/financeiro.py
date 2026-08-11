import calendar
import csv
import io
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import (
    CategoriaFinanceira,
    EmpresaCRM,
    EventoAuditoria,
    LancamentoFinanceiro,
    Lead,
    ParcelaFinanceira,
    ProcessoMonitorado,
    StatusLead,
)
from app.proxy import cliente_ip

router = APIRouter(prefix="/v1/admin/financeiro", tags=["financeiro"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.manage"))]
ApproveDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.approve"))]
ExportDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.export"))]
STATUS_CLIENTE = frozenset({StatusLead.PROPOSTA_ENVIADA, StatusLead.CONVERTIDO})


class CategoriaCreate(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    tipo: Literal["pagar", "receber", "ambos"] = "ambos"


class LancamentoCreate(BaseModel):
    tipo: Literal["pagar", "receber"]
    descricao: str = Field(min_length=3, max_length=240)
    documento: str | None = Field(default=None, max_length=80)
    competencia: date
    valor_total: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    primeiro_vencimento: date
    quantidade_parcelas: int = Field(default=1, ge=1, le=120)
    empresa_id: int | None = None
    categoria_id: int | None = None
    observacoes: str | None = Field(default=None, max_length=4000)


class BaixaCreate(BaseModel):
    valor_pago: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    pago_em: date
    forma_pagamento: str = Field(min_length=2, max_length=50)
    observacoes: str | None = Field(default=None, max_length=1000)


class Justificativa(BaseModel):
    motivo: str = Field(min_length=5, max_length=1000)


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
            ator=usuario.ator,
            acao=acao[:20],
            recurso=recurso[:180],
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes=detalhes,
        )
    )


def _mes_seguinte(valor: date, meses: int) -> date:
    indice = valor.month - 1 + meses
    ano, mes = valor.year + indice // 12, indice % 12 + 1
    return date(ano, mes, min(valor.day, calendar.monthrange(ano, mes)[1]))


def _parcelar(total: Decimal, quantidade: int) -> list[Decimal]:
    centavos = Decimal("0.01")
    base = (total / quantidade).quantize(centavos, rounding=ROUND_DOWN)
    valores = [base] * quantidade
    valores[-1] += total - sum(valores)
    return valores


def _empresa_cliente(usuario: UsuarioAutenticado):
    lead_elegivel = exists(
        select(Lead.id).where(
            Lead.empresa_id == EmpresaCRM.id,
            Lead.organizacao_id == usuario.organizacao_id,
            Lead.status.in_(STATUS_CLIENTE),
        )
    )
    processo_monitorado = exists(
        select(ProcessoMonitorado.id).where(
            ProcessoMonitorado.empresa_id == EmpresaCRM.id,
            ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
        )
    )
    return or_(lead_elegivel, processo_monitorado)


async def _atualizar_status(lancamento: LancamentoFinanceiro) -> None:
    if lancamento.status == "cancelado":
        return
    abertas = [p for p in lancamento.parcelas if p.status != "paga"]
    pagas = len(lancamento.parcelas) - len(abertas)
    lancamento.status = "pago" if not abertas else ("parcial" if pagas else "aberto")
    lancamento.atualizado_em = datetime.now(UTC)


def _serializar(lancamento: LancamentoFinanceiro) -> dict:
    hoje = date.today()
    parcelas = [
        {
            "id": p.id,
            "numero": p.numero,
            "vencimento": p.vencimento,
            "valor": float(p.valor),
            "valor_pago": float(p.valor_pago or 0),
            "status": "atrasada" if p.status == "aberta" and p.vencimento < hoje else p.status,
            "pago_em": p.pago_em,
            "forma_pagamento": p.forma_pagamento,
        }
        for p in lancamento.parcelas
    ]
    return {
        "id": lancamento.id,
        "tipo": lancamento.tipo,
        "descricao": lancamento.descricao,
        "documento": lancamento.documento,
        "competencia": lancamento.competencia,
        "valor_total": float(lancamento.valor_total),
        "status": lancamento.status,
        "empresa_id": lancamento.empresa_id,
        "empresa": lancamento.empresa_registro.nome if lancamento.empresa_registro else None,
        "categoria_id": lancamento.categoria_id,
        "categoria": lancamento.categoria.nome if lancamento.categoria else None,
        "observacoes": lancamento.observacoes,
        "criado_em": lancamento.criado_em,
        "parcelas": parcelas,
    }


def _filtros(
    usuario: UsuarioAutenticado,
    tipo: str | None,
    status: str | None,
    inicio: date | None,
    fim: date | None,
    busca: str | None,
):
    itens = [LancamentoFinanceiro.organizacao_id == usuario.organizacao_id]
    if tipo:
        itens.append(LancamentoFinanceiro.tipo == tipo)
    if status:
        itens.append(LancamentoFinanceiro.status == status)
    if inicio:
        itens.append(ParcelaFinanceira.vencimento >= inicio)
    if fim:
        itens.append(ParcelaFinanceira.vencimento <= fim)
    if busca:
        termo = f"%{busca.strip()}%"
        itens.append(
            or_(
                LancamentoFinanceiro.descricao.ilike(termo),
                LancamentoFinanceiro.documento.ilike(termo),
                EmpresaCRM.nome.ilike(termo),
            )
        )
    return itens


@router.get("")
async def listar(
    session: SessionDep,
    usuario: ViewDep,
    tipo: Literal["pagar", "receber"] | None = None,
    status: Literal["aberto", "parcial", "pago", "cancelado"] | None = None,
    inicio: date | None = None,
    fim: date | None = None,
    busca: Annotated[str | None, Query(max_length=120)] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict:
    filtros = _filtros(usuario, tipo, status, inicio, fim, busca)
    consulta = (
        select(LancamentoFinanceiro)
        .options(
            selectinload(LancamentoFinanceiro.parcelas),
            selectinload(LancamentoFinanceiro.empresa_registro),
            selectinload(LancamentoFinanceiro.categoria),
        )
        .outerjoin(EmpresaCRM, EmpresaCRM.id == LancamentoFinanceiro.empresa_id)
        .outerjoin(ParcelaFinanceira, ParcelaFinanceira.lancamento_id == LancamentoFinanceiro.id)
        .where(*filtros)
        .distinct()
        .order_by(LancamentoFinanceiro.criado_em.desc())
        .limit(limite)
    )
    itens = (await session.execute(consulta)).scalars().all()
    hoje = date.today()
    todas = (
        await session.execute(
            select(ParcelaFinanceira, LancamentoFinanceiro.tipo)
            .join(LancamentoFinanceiro)
            .where(
                ParcelaFinanceira.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.status != "cancelado",
            )
        )
    ).all()
    resumo = {
        "receber_aberto": Decimal(0),
        "pagar_aberto": Decimal(0),
        "recebido_mes": Decimal(0),
        "pago_mes": Decimal(0),
        "vencido": Decimal(0),
    }
    for parcela, natureza in todas:
        restante = Decimal(parcela.valor) - Decimal(parcela.valor_pago or 0)
        if parcela.status != "paga":
            resumo[f"{natureza}_aberto"] += restante
            if parcela.vencimento < hoje:
                resumo["vencido"] += restante
        elif (
            parcela.pago_em
            and parcela.pago_em.year == hoje.year
            and parcela.pago_em.month == hoje.month
        ):
            resumo["recebido_mes" if natureza == "receber" else "pago_mes"] += Decimal(
                parcela.valor_pago
            )
    return {
        "resumo": {k: float(v) for k, v in resumo.items()},
        "itens": [_serializar(x) for x in itens],
        "total": len(itens),
    }


@router.get("/referencias")
async def referencias(session: SessionDep, usuario: ViewDep) -> dict:
    empresas = (
        (
            await session.execute(
                select(EmpresaCRM)
                .where(
                    EmpresaCRM.organizacao_id == usuario.organizacao_id,
                    _empresa_cliente(usuario),
                )
                .distinct()
                .order_by(EmpresaCRM.nome)
                .limit(300)
            )
        )
        .scalars()
        .all()
    )
    categorias = (
        (
            await session.execute(
                select(CategoriaFinanceira)
                .where(
                    CategoriaFinanceira.organizacao_id == usuario.organizacao_id,
                    CategoriaFinanceira.ativo.is_(True),
                )
                .order_by(CategoriaFinanceira.nome)
            )
        )
        .scalars()
        .all()
    )
    return {
        "empresas": [{"id": x.id, "nome": x.nome} for x in empresas],
        "categorias": [{"id": x.id, "nome": x.nome, "tipo": x.tipo} for x in categorias],
    }


@router.post("/categorias", status_code=201)
async def criar_categoria(
    dados: CategoriaCreate, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    existente = (
        await session.execute(
            select(CategoriaFinanceira.id).where(
                CategoriaFinanceira.organizacao_id == usuario.organizacao_id,
                func.lower(CategoriaFinanceira.nome) == dados.nome.strip().lower(),
                CategoriaFinanceira.tipo == dados.tipo,
            )
        )
    ).scalar_one_or_none()
    if existente:
        raise HTTPException(409, "Categoria já cadastrada")
    categoria = CategoriaFinanceira(
        organizacao_id=usuario.organizacao_id, nome=dados.nome.strip(), tipo=dados.tipo
    )
    session.add(categoria)
    await session.flush()
    _auditar(
        session,
        request,
        usuario,
        "criar_categoria",
        f"categoria-financeira:{categoria.id}",
        {"nome": categoria.nome, "tipo": categoria.tipo},
    )
    await session.commit()
    return {"id": categoria.id, "nome": categoria.nome, "tipo": categoria.tipo}


@router.post("/lancamentos", status_code=201)
async def criar_lancamento(
    dados: LancamentoCreate, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    if dados.empresa_id:
        cliente_elegivel = (
            await session.execute(
                select(EmpresaCRM.id).where(
                    EmpresaCRM.id == dados.empresa_id,
                    EmpresaCRM.organizacao_id == usuario.organizacao_id,
                    _empresa_cliente(usuario),
                )
            )
        ).scalar_one_or_none()
        if not cliente_elegivel:
            raise HTTPException(
                422,
                "Empresa ainda não é cliente: requer proposta enviada, "
                "conversão ou processo monitorado",
            )
    if dados.categoria_id:
        categoria = (
            await session.execute(
                select(CategoriaFinanceira).where(
                    CategoriaFinanceira.id == dados.categoria_id,
                    CategoriaFinanceira.organizacao_id == usuario.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if not categoria:
            raise HTTPException(404, "Categoria não encontrada")
        if categoria.tipo not in {"ambos", dados.tipo}:
            raise HTTPException(422, "Categoria incompatível com o tipo do lançamento")
    lancamento = LancamentoFinanceiro(
        organizacao_id=usuario.organizacao_id,
        empresa_id=dados.empresa_id,
        categoria_id=dados.categoria_id,
        tipo=dados.tipo,
        descricao=dados.descricao.strip(),
        documento=(dados.documento or "").strip() or None,
        competencia=dados.competencia,
        valor_total=dados.valor_total,
        observacoes=(dados.observacoes or "").strip() or None,
        criado_por_id=usuario.id,
        criado_por=usuario.ator,
    )
    session.add(lancamento)
    await session.flush()
    for indice, valor in enumerate(_parcelar(dados.valor_total, dados.quantidade_parcelas)):
        session.add(
            ParcelaFinanceira(
                organizacao_id=usuario.organizacao_id,
                lancamento_id=lancamento.id,
                numero=indice + 1,
                vencimento=_mes_seguinte(dados.primeiro_vencimento, indice),
                valor=valor,
                valor_pago=Decimal(0),
            )
        )
    _auditar(
        session,
        request,
        usuario,
        "criar_lancamento",
        f"lancamento-financeiro:{lancamento.id}",
        {
            "tipo": dados.tipo,
            "valor": str(dados.valor_total),
            "parcelas": dados.quantidade_parcelas,
        },
    )
    await session.commit()
    return {"id": lancamento.id, "status": "criado"}


async def _parcela(
    session: AsyncSession, usuario: UsuarioAutenticado, parcela_id: int
) -> ParcelaFinanceira:
    parcela = (
        await session.execute(
            select(ParcelaFinanceira)
            .options(
                selectinload(ParcelaFinanceira.lancamento).selectinload(
                    LancamentoFinanceiro.parcelas
                )
            )
            .where(
                ParcelaFinanceira.id == parcela_id,
                ParcelaFinanceira.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if not parcela:
        raise HTTPException(404, "Parcela não encontrada")
    return parcela


@router.post("/parcelas/{parcela_id}/baixar")
async def baixar(
    parcela_id: int, dados: BaixaCreate, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    parcela = await _parcela(session, usuario, parcela_id)
    if parcela.lancamento.status == "cancelado" or parcela.status == "paga":
        raise HTTPException(409, "Parcela não pode ser baixada")
    if dados.valor_pago != Decimal(parcela.valor):
        raise HTTPException(422, "Neste MVP, a baixa deve usar o valor integral da parcela")
    parcela.valor_pago, parcela.pago_em, parcela.forma_pagamento = (
        dados.valor_pago,
        dados.pago_em,
        dados.forma_pagamento.strip(),
    )
    parcela.observacoes_baixa, parcela.status = (dados.observacoes or "").strip() or None, "paga"
    await _atualizar_status(parcela.lancamento)
    _auditar(
        session,
        request,
        usuario,
        "baixar_parcela",
        f"parcela-financeira:{parcela.id}",
        {"valor": str(dados.valor_pago), "data": str(dados.pago_em)},
    )
    await session.commit()
    return {"status": "paga"}


@router.post("/parcelas/{parcela_id}/estornar")
async def estornar(
    parcela_id: int,
    dados: Justificativa,
    request: Request,
    session: SessionDep,
    usuario: ApproveDep,
) -> dict:
    parcela = await _parcela(session, usuario, parcela_id)
    if parcela.status != "paga":
        raise HTTPException(409, "Somente parcelas pagas podem ser estornadas")
    anterior = str(parcela.valor_pago)
    parcela.valor_pago, parcela.pago_em, parcela.forma_pagamento = Decimal(0), None, None
    parcela.observacoes_baixa, parcela.status = None, "aberta"
    await _atualizar_status(parcela.lancamento)
    _auditar(
        session,
        request,
        usuario,
        "estornar_baixa",
        f"parcela-financeira:{parcela.id}",
        {"valor_anterior": anterior, "motivo": dados.motivo},
    )
    await session.commit()
    return {"status": "aberta"}


@router.post("/lancamentos/{lancamento_id}/cancelar")
async def cancelar(
    lancamento_id: int,
    dados: Justificativa,
    request: Request,
    session: SessionDep,
    usuario: ApproveDep,
) -> dict:
    lancamento = (
        await session.execute(
            select(LancamentoFinanceiro)
            .options(selectinload(LancamentoFinanceiro.parcelas))
            .where(
                LancamentoFinanceiro.id == lancamento_id,
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if not lancamento:
        raise HTTPException(404, "Lançamento não encontrado")
    if lancamento.status == "cancelado" or any(p.status == "paga" for p in lancamento.parcelas):
        raise HTTPException(409, "Estorne as baixas antes de cancelar")
    lancamento.status, lancamento.cancelado_em = "cancelado", datetime.now(UTC)
    lancamento.cancelado_por, lancamento.cancelamento_motivo = usuario.ator, dados.motivo.strip()
    _auditar(
        session,
        request,
        usuario,
        "cancelar_lancamento",
        f"lancamento-financeiro:{lancamento.id}",
        {"motivo": dados.motivo},
    )
    await session.commit()
    return {"status": "cancelado"}


@router.get("/exportar.csv")
async def exportar(session: SessionDep, usuario: ExportDep) -> StreamingResponse:
    itens = (
        (
            await session.execute(
                select(LancamentoFinanceiro)
                .options(
                    selectinload(LancamentoFinanceiro.parcelas),
                    selectinload(LancamentoFinanceiro.empresa_registro),
                    selectinload(LancamentoFinanceiro.categoria),
                )
                .where(LancamentoFinanceiro.organizacao_id == usuario.organizacao_id)
                .order_by(LancamentoFinanceiro.id)
            )
        )
        .scalars()
        .all()
    )
    arquivo = io.StringIO()
    writer = csv.writer(arquivo, delimiter=";")
    writer.writerow(
        [
            "ID",
            "Tipo",
            "Descrição",
            "Empresa",
            "Categoria",
            "Status",
            "Valor total",
            "Parcela",
            "Vencimento",
            "Valor parcela",
            "Valor pago",
            "Data baixa",
        ]
    )
    for item in itens:
        for p in item.parcelas:
            writer.writerow(
                [
                    item.id,
                    item.tipo,
                    item.descricao,
                    item.empresa_registro.nome if item.empresa_registro else "",
                    item.categoria.nome if item.categoria else "",
                    item.status,
                    item.valor_total,
                    p.numero,
                    p.vencimento,
                    p.valor,
                    p.valor_pago,
                    p.pago_em or "",
                ]
            )
    return StreamingResponse(
        iter(("\ufeff" + arquivo.getvalue(),)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="financeiro.csv"'},
    )

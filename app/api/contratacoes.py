import hashlib
import hmac
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.leads import sincronizar_pagamento_proposta_por_id
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import (
    ContratacaoServico,
    EventoCobrancaSandbox,
    GuiaInpi,
    LancamentoFinanceiro,
    ParcelaFinanceira,
    PropostaComercial,
    ReciboFinanceiro,
    RenovacaoFinanceira,
    ServicoFinanceiro,
)
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

router = APIRouter(prefix="/v1/admin/financeiro", tags=["contratacoes financeiras"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.manage"))]


class ServicoInput(BaseModel):
    codigo: str = Field(min_length=2, max_length=50)
    nome: str = Field(min_length=2, max_length=180)
    descricao: str | None = None
    valor: Decimal = Field(ge=0)
    recorrente: bool = False


class ContratacaoInput(BaseModel):
    servico_id: int
    lead_id: int | None = None
    processo_id: int | None = None
    proposta_id: int | None = None
    parcelas: int = Field(default=1, ge=1, le=60)
    primeiro_vencimento: date | None = None
    idempotency_key: str = Field(min_length=8, max_length=120)


class GatewayWebhookInput(BaseModel):
    organizacao_id: int = Field(ge=1)
    referencia: str = Field(min_length=8, max_length=100)
    parcela_id: int = Field(ge=1)
    status: str = Field(pattern="^(paid|failed|refunded)$")
    valor: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


class GuiaFinanceiraInput(BaseModel):
    lead_id: int
    descricao: str = Field(min_length=2, max_length=200)
    valor: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    processo_id: int | None = None
    proposta_id: int | None = None
    vencimento: date | None = None


class RenovacaoInput(BaseModel):
    processo_id: int
    referencia: str = Field(min_length=2, max_length=40)
    vencimento: date
    tipo: str = Field(default="renovacao", max_length=30)


@router.get("/servicos")
async def listar_servicos(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (
            await session.execute(
                select(ServicoFinanceiro)
                .where(
                    ServicoFinanceiro.organizacao_id == usuario.organizacao_id,
                    ServicoFinanceiro.ativo.is_(True),
                )
                .order_by(ServicoFinanceiro.nome)
            )
        )
        .scalars()
        .all()
    )
    return {
        "servicos": [
            {
                "id": i.id,
                "codigo": i.codigo,
                "nome": i.nome,
                "descricao": i.descricao,
                "valor": i.valor,
                "recorrente": i.recorrente,
            }
            for i in itens
        ]
    }


@router.post("/servicos", status_code=201)
async def criar_servico(dados: ServicoInput, session: SessionDep, usuario: ManageDep) -> dict:
    item = ServicoFinanceiro(organizacao_id=usuario.organizacao_id, **dados.model_dump())
    session.add(item)
    await session.commit()
    return {"id": item.id, "codigo": item.codigo, "nome": item.nome, "valor": item.valor}


@router.post("/contratacoes", status_code=201)
async def contratar_servico(dados: ContratacaoInput, session: SessionDep, usuario: ManageDep) -> dict:
    if dados.lead_id is None and dados.processo_id is None and dados.proposta_id is None:
        raise HTTPException(
            status_code=422,
            detail="A contratacao deve estar vinculada a proposta, oportunidade ou processo",
        )
    existente = (
        await session.execute(
            select(LancamentoFinanceiro).where(
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.idempotency_key == dados.idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existente:
        return {
            "idempotente": True,
            "lancamento_id": existente.id,
            "parcelas": len(existente.parcelas),
        }
    servico = (
        await session.execute(
            select(ServicoFinanceiro).where(
                ServicoFinanceiro.id == dados.servico_id,
                ServicoFinanceiro.organizacao_id == usuario.organizacao_id,
                ServicoFinanceiro.ativo.is_(True),
            )
        )
    ).scalar_one_or_none()
    if servico is None:
        raise HTTPException(status_code=404, detail="ServiÃ§o nÃ£o encontrado")
    total = Decimal(servico.valor)
    if dados.proposta_id:
        proposta = (
            await session.execute(
                select(PropostaComercial).where(
                    PropostaComercial.id == dados.proposta_id,
                    PropostaComercial.organizacao_id == usuario.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if proposta is None:
            raise HTTPException(status_code=404, detail="Proposta nao encontrada")
        if dados.lead_id and proposta.lead_id != dados.lead_id:
            raise HTTPException(status_code=422, detail="Proposta nao pertence ao lead informado")
    lancamento = LancamentoFinanceiro(
        organizacao_id=usuario.organizacao_id,
        lead_id=dados.lead_id,
        processo_id=dados.processo_id,
        proposta_id=dados.proposta_id,
        idempotency_key=dados.idempotency_key,
        tipo="receber",
        descricao=servico.nome,
        competencia=date.today(),
        valor_total=total,
        status="aberto",
        criado_por_id=usuario.id,
        criado_por=usuario.nome or usuario.email,
    )
    session.add(lancamento)
    await session.flush()
    inicio = dados.primeiro_vencimento or date.today()
    valor_parcela = (total / dados.parcelas).quantize(Decimal("0.01"))
    for numero in range(1, dados.parcelas + 1):
        valor = total - valor_parcela * (dados.parcelas - 1) if numero == dados.parcelas else valor_parcela
        session.add(
            ParcelaFinanceira(
                organizacao_id=usuario.organizacao_id,
                lancamento_id=lancamento.id,
                numero=numero,
                vencimento=inicio + timedelta(days=30 * (numero - 1)),
                valor=valor,
            )
        )
    contratacao = ContratacaoServico(
        organizacao_id=usuario.organizacao_id,
        servico_id=servico.id,
        lead_id=dados.lead_id,
        processo_id=dados.processo_id,
        proposta_id=dados.proposta_id,
        lancamento_id=lancamento.id,
    )
    session.add(contratacao)
    await session.commit()
    return {
        "idempotente": False,
        "contratacao_id": contratacao.id,
        "lancamento_id": lancamento.id,
        "parcelas": dados.parcelas,
    }


@router.get("/inadimplencia")
async def listar_inadimplencia(session: SessionDep, usuario: ViewDep) -> dict:
    hoje = date.today()
    itens = (
        (
            await session.execute(
                select(ParcelaFinanceira)
                .join(LancamentoFinanceiro)
                .where(
                    ParcelaFinanceira.organizacao_id == usuario.organizacao_id,
                    LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
                    ParcelaFinanceira.status == "aberta",
                    ParcelaFinanceira.vencimento < hoje,
                )
                .order_by(ParcelaFinanceira.vencimento)
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "parcela_id": p.id,
                "lancamento_id": p.lancamento_id,
                "vencimento": p.vencimento,
                "valor": p.valor,
                "dias_atraso": (hoje - p.vencimento).days,
            }
            for p in itens
        ],
        "total": len(itens),
    }


@router.post("/guias", status_code=201)
async def criar_guia_financeira(dados: GuiaFinanceiraInput, session: SessionDep, usuario: ManageDep) -> dict:
    if dados.processo_id is None and dados.proposta_id is None:
        raise HTTPException(status_code=422, detail="GRU deve estar vinculada a proposta ou processo")
    guia = GuiaInpi(
        organizacao_id=usuario.organizacao_id,
        lead_id=dados.lead_id,
        processo_id=dados.processo_id,
        proposta_id=dados.proposta_id,
        descricao=dados.descricao.strip(),
        valor=dados.valor,
        vencimento=dados.vencimento,
        status="pendente",
    )
    session.add(guia)
    await session.commit()
    return {"id": guia.id, "status": guia.status}


@router.post("/renovacoes", status_code=201)
async def criar_renovacao(dados: RenovacaoInput, session: SessionDep, usuario: ManageDep) -> dict:
    existente = (
        await session.execute(
            select(RenovacaoFinanceira).where(
                RenovacaoFinanceira.organizacao_id == usuario.organizacao_id,
                RenovacaoFinanceira.processo_id == dados.processo_id,
                RenovacaoFinanceira.tipo == dados.tipo,
                RenovacaoFinanceira.referencia == dados.referencia,
            )
        )
    ).scalar_one_or_none()
    if existente:
        return {"id": existente.id, "idempotente": True, "status": existente.status}
    item = RenovacaoFinanceira(organizacao_id=usuario.organizacao_id, **dados.model_dump())
    session.add(item)
    await session.commit()
    return {"id": item.id, "idempotente": False, "status": item.status}


@router.post("/parcelas/{parcela_id}/recibo", status_code=201)
async def emitir_recibo(parcela_id: int, session: SessionDep, usuario: ManageDep) -> dict:
    parcela = (
        await session.execute(
            select(ParcelaFinanceira).where(
                ParcelaFinanceira.id == parcela_id,
                ParcelaFinanceira.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if parcela is None or parcela.status != "paga":
        raise HTTPException(status_code=409, detail="Somente parcela paga pode gerar recibo")
    existente = (
        await session.execute(
            select(ReciboFinanceiro).where(
                ReciboFinanceiro.organizacao_id == usuario.organizacao_id,
                ReciboFinanceiro.parcela_id == parcela_id,
            )
        )
    ).scalar_one_or_none()
    if existente:
        return {"id": existente.id, "numero": existente.numero, "idempotente": True}
    numero = f"REC-{usuario.organizacao_id}-{parcela_id}"
    recibo = ReciboFinanceiro(
        organizacao_id=usuario.organizacao_id,
        parcela_id=parcela_id,
        numero=numero,
        dados={"valor": str(parcela.valor_pago), "pago_em": str(parcela.pago_em)},
    )
    session.add(recibo)
    await session.commit()
    return {"id": recibo.id, "numero": recibo.numero, "idempotente": False}


@router.post("/gateway/webhook")
async def receber_webhook_gateway(
    dados: GatewayWebhookInput,
    request: Request,
    session: SessionDep,
    x_gateway_signature: str | None = Header(default=None),
) -> dict:
    """Concilia eventos assinados do gateway sem duplicar baixas."""
    segredo = get_settings().gateway_webhook_secret
    if not segredo:
        raise HTTPException(status_code=503, detail="Gateway nao configurado")
    corpo = await request.body()
    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    if not x_gateway_signature or not hmac.compare_digest(x_gateway_signature, esperado):
        raise HTTPException(status_code=401, detail="Assinatura do gateway invalida")
    await aplicar_contexto_tenant(session, dados.organizacao_id)
    evento = (
        await session.execute(
            select(EventoCobrancaSandbox).where(
                EventoCobrancaSandbox.organizacao_id == dados.organizacao_id,
                EventoCobrancaSandbox.referencia == dados.referencia,
            )
        )
    ).scalar_one_or_none()
    if evento is not None:
        return {"idempotente": True, "status": evento.status}
    parcela = (
        await session.execute(
            select(ParcelaFinanceira)
            .where(
                ParcelaFinanceira.id == dados.parcela_id,
                ParcelaFinanceira.organizacao_id == dados.organizacao_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if parcela is None:
        raise HTTPException(status_code=404, detail="Parcela nao encontrada")
    evento = EventoCobrancaSandbox(
        organizacao_id=dados.organizacao_id,
        tipo="gateway",
        status=dados.status,
        referencia=dados.referencia,
        detalhes={"parcela_id": dados.parcela_id, "valor": str(dados.valor)},
    )
    session.add(evento)
    if dados.status == "paid":
        if Decimal(parcela.valor) != dados.valor:
            raise HTTPException(status_code=422, detail="Valor do gateway diverge da parcela")
        parcela.valor_pago = dados.valor
        parcela.pago_em = datetime.now(UTC).date()
        parcela.status = "paga"
        parcela.lancamento.status = (
            "pago" if all(item.status == "paga" for item in parcela.lancamento.parcelas) else "parcial"
        )
        if parcela.lancamento.proposta_id:
            await sincronizar_pagamento_proposta_por_id(session, dados.organizacao_id, parcela.lancamento.proposta_id)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        repetido = (
            await session.execute(
                select(EventoCobrancaSandbox).where(
                    EventoCobrancaSandbox.organizacao_id == dados.organizacao_id,
                    EventoCobrancaSandbox.referencia == dados.referencia,
                )
            )
        ).scalar_one_or_none()
        if repetido is not None:
            return {"idempotente": True, "status": repetido.status}
        raise
    return {"idempotente": False, "status": dados.status, "parcela_id": parcela.id}

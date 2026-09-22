import calendar
import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.leads_propostas import sincronizar_pagamento_proposta_por_id
from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.crm import normalizar_empresa, registrar_evento_operacional
from app.database import get_session
from app.models import (
    CategoriaFinanceira,
    ComissaoFinanceira,
    CustoJuridico,
    EmpresaCRM,
    EventoAuditoria,
    FormaPagamentoFinanceira,
    HistoricoFinanceiro,
    LancamentoFinanceiro,
    Lead,
    ParcelaFinanceira,
    PlanoContas,
    Processo,
    ProcessoMonitorado,
    RetribuicaoInpi,
    StatusLead,
    UsuarioOperacoes,
)
from app.plano_contas import CONTAS_PADRAO, GRUPOS_DRE, montar_dre
from app.proxy import cliente_ip

router = APIRouter(prefix="/v1/admin/financeiro", tags=["financeiro"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.manage"))]
ApproveDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.approve"))]
ExportDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.export"))]
STATUS_CLIENTE = frozenset({StatusLead.PROPOSTA_ENVIADA, StatusLead.CONVERTIDO})
PERFIS_LOG_FINANCEIRO = frozenset({"administrador", "tech", "ceo", "financeiro"})


async def exigir_acesso_log_financeiro(usuario: ViewDep) -> UsuarioAutenticado:
    if usuario.perfil not in PERFIS_LOG_FINANCEIRO:
        raise HTTPException(403, "Log financeiro disponível somente para Administrador, Tech, CEO e Financeiro")
    return usuario


LogDep = Annotated[UsuarioAutenticado, Depends(exigir_acesso_log_financeiro)]


class CategoriaCreate(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    tipo: Literal["pagar", "receber", "ambos"] = "ambos"
    # Pedido do usuário (22/09/2026): subcategoria. Ignorado (sobrescrito
    # pelo tipo do pai) quando categoria_pai_id é informado -- ver
    # _salvar_categoria.
    categoria_pai_id: int | None = Field(default=None, ge=1)
    ativo: bool = True


class PlanoContasCreate(BaseModel):
    codigo: str = Field(min_length=1, max_length=20)
    nome: str = Field(min_length=2, max_length=150)
    natureza: Literal["receita", "despesa"]
    grupo_dre: Literal[*GRUPOS_DRE]  # type: ignore[valid-type]
    conta_pai_id: int | None = None


class FormaPagamentoCreate(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    tipo: Literal["pix", "boleto", "transferencia", "cartao_credito", "cartao_debito", "dinheiro", "outro"] = "outro"
    permite_parcelamento: bool = False
    maximo_parcelas: int = Field(default=1, ge=1, le=120)
    ativo: bool = True


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
    conta_contabil_id: int | None = None
    forma_pagamento_id: int | None = None
    observacoes: str | None = Field(default=None, max_length=4000)


class EmpresaFinanceiraCreate(BaseModel):
    nome: str = Field(min_length=2, max_length=200)
    documento: str | None = Field(default=None, max_length=18)
    email: str | None = Field(default=None, max_length=254)
    telefone: str | None = Field(default=None, max_length=30)
    segmento: str | None = Field(default=None, max_length=80)
    observacoes: str | None = Field(default=None, max_length=2000)


class LancamentoUpdate(BaseModel):
    descricao: str = Field(min_length=3, max_length=240)
    documento: str | None = Field(default=None, max_length=80)
    competencia: date
    valor_total: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    primeiro_vencimento: date
    quantidade_parcelas: int = Field(default=1, ge=1, le=120)
    empresa_id: int | None = None
    categoria_id: int | None = None
    conta_contabil_id: int | None = None
    forma_pagamento_id: int | None = None
    observacoes: str | None = Field(default=None, max_length=4000)


class BaixaCreate(BaseModel):
    valor_pago: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    pago_em: date
    forma_pagamento_id: int
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


def _registrar_historico(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    lancamento_id: int,
    acao: str,
    descricao: str,
    detalhes: dict,
    parcela_id: int | None = None,
) -> None:
    session.add(
        HistoricoFinanceiro(
            organizacao_id=usuario.organizacao_id,
            lancamento_id=lancamento_id,
            parcela_id=parcela_id,
            acao=acao,
            ator=usuario.ator,
            descricao=descricao[:500],
            detalhes=detalhes,
        )
    )
    registrar_evento_operacional(
        session,
        organizacao_id=usuario.organizacao_id,
        dominio="financeiro",
        tipo=f"financeiro.{acao}",
        entidade_tipo="parcela" if parcela_id else "lancamento",
        entidade_id=parcela_id or lancamento_id,
        ator=usuario.ator,
        ator_id=usuario.id,
        payload={"lancamento_id": lancamento_id, **detalhes},
    )


async def _forma_pagamento(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    forma_id: int | None,
    *,
    exigir_ativa: bool = True,
) -> FormaPagamentoFinanceira | None:
    if not forma_id:
        return None
    filtros = [
        FormaPagamentoFinanceira.id == forma_id,
        FormaPagamentoFinanceira.organizacao_id == usuario.organizacao_id,
    ]
    if exigir_ativa:
        filtros.append(FormaPagamentoFinanceira.ativo.is_(True))
    forma = (await session.execute(select(FormaPagamentoFinanceira).where(*filtros))).scalar_one_or_none()
    if not forma:
        raise HTTPException(404, "Forma de pagamento não encontrada ou inativa")
    return forma


def _validar_parcelamento(forma: FormaPagamentoFinanceira | None, quantidade: int) -> None:
    if not forma:
        return
    limite = forma.maximo_parcelas if forma.permite_parcelamento else 1
    if quantidade > limite:
        raise HTTPException(422, f"{forma.nome} permite no máximo {limite} parcela(s)")


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


async def _validar_referencias(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    tipo: str,
    empresa_id: int | None,
    categoria_id: int | None,
    conta_contabil_id: int | None = None,
) -> None:
    if empresa_id:
        filtros_empresa = [
            EmpresaCRM.id == empresa_id,
            EmpresaCRM.organizacao_id == usuario.organizacao_id,
        ]
        if tipo == "receber":
            filtros_empresa.append(_empresa_cliente(usuario))
        cliente_elegivel = (await session.execute(select(EmpresaCRM.id).where(*filtros_empresa))).scalar_one_or_none()
        if not cliente_elegivel:
            detalhe = (
                "Empresa ainda não é cliente: requer proposta enviada, conversão ou processo monitorado"
                if tipo == "receber"
                else "Empresa ou fornecedor não encontrado"
            )
            raise HTTPException(422, detalhe)
    if categoria_id:
        categoria = (
            await session.execute(
                select(CategoriaFinanceira).where(
                    CategoriaFinanceira.id == categoria_id,
                    CategoriaFinanceira.organizacao_id == usuario.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if not categoria:
            raise HTTPException(404, "Categoria não encontrada")
        if categoria.tipo not in {"ambos", tipo}:
            raise HTTPException(422, "Categoria incompatível com o tipo do lançamento")
    if conta_contabil_id:
        conta = (
            await session.execute(
                select(PlanoContas.id).where(
                    PlanoContas.id == conta_contabil_id,
                    PlanoContas.organizacao_id == usuario.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if not conta:
            raise HTTPException(404, "Conta contábil não encontrada")


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
        "lead_id": lancamento.lead_id,
        "proposta_id": lancamento.proposta_id,
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
        "forma_pagamento_id": lancamento.forma_pagamento_id,
        "forma_pagamento": lancamento.forma_pagamento.nome if lancamento.forma_pagamento else None,
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
            selectinload(LancamentoFinanceiro.forma_pagamento),
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
    resumo_filtros = [
        ParcelaFinanceira.organizacao_id == usuario.organizacao_id,
        LancamentoFinanceiro.status != "cancelado",
    ]
    if tipo:
        resumo_filtros.append(LancamentoFinanceiro.tipo == tipo)
    todas = (
        await session.execute(
            select(ParcelaFinanceira, LancamentoFinanceiro.tipo).join(LancamentoFinanceiro).where(*resumo_filtros)
        )
    ).all()
    resumo = {
        "receber_aberto": Decimal(0),
        "pagar_aberto": Decimal(0),
        "recebido_mes": Decimal(0),
        "pago_mes": Decimal(0),
        "vencido": Decimal(0),
        "receber_vencido": Decimal(0),
        "pagar_vencido": Decimal(0),
        "vence_hoje": Decimal(0),
        "vence_7_dias": Decimal(0),
        "parcelas_vencidas": 0,
        "parcelas_hoje": 0,
        "parcelas_7_dias": 0,
    }
    for parcela, natureza in todas:
        restante = Decimal(parcela.valor) - Decimal(parcela.valor_pago or 0)
        if parcela.status != "paga":
            resumo[f"{natureza}_aberto"] += restante
            if parcela.vencimento < hoje:
                resumo["vencido"] += restante
                resumo[f"{natureza}_vencido"] += restante
                resumo["parcelas_vencidas"] += 1
            elif parcela.vencimento == hoje:
                resumo["vence_hoje"] += restante
                resumo["parcelas_hoje"] += 1
            elif parcela.vencimento <= hoje + timedelta(days=7):
                resumo["vence_7_dias"] += restante
                resumo["parcelas_7_dias"] += 1
        elif parcela.pago_em and parcela.pago_em.year == hoje.year and parcela.pago_em.month == hoje.month:
            resumo["recebido_mes" if natureza == "receber" else "pago_mes"] += Decimal(parcela.valor_pago)
    return {
        "resumo": {k: int(v) if k.startswith("parcelas_") else float(v) for k, v in resumo.items()},
        "itens": [_serializar(x) for x in itens],
        "total": len(itens),
    }


@router.get("/referencias")
async def referencias(
    session: SessionDep,
    usuario: ViewDep,
    tipo: Literal["pagar", "receber"] = "receber",
) -> dict:
    filtros_empresas = [EmpresaCRM.organizacao_id == usuario.organizacao_id]
    if tipo == "receber":
        filtros_empresas.append(_empresa_cliente(usuario))
    empresas = (
        (
            await session.execute(
                select(EmpresaCRM).where(*filtros_empresas).distinct().order_by(EmpresaCRM.nome).limit(300)
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
    formas = (
        (
            await session.execute(
                select(FormaPagamentoFinanceira)
                .where(
                    FormaPagamentoFinanceira.organizacao_id == usuario.organizacao_id,
                    FormaPagamentoFinanceira.ativo.is_(True),
                )
                .order_by(FormaPagamentoFinanceira.nome)
            )
        )
        .scalars()
        .all()
    )
    return {
        "empresas": [{"id": x.id, "nome": x.nome} for x in empresas],
        "categorias": [
            {"id": x.id, "nome": x.nome, "tipo": x.tipo, "categoria_pai_id": x.categoria_pai_id} for x in categorias
        ],
        "formas_pagamento": [
            {
                "id": x.id,
                "nome": x.nome,
                "tipo": x.tipo,
                "permite_parcelamento": x.permite_parcelamento,
                "maximo_parcelas": x.maximo_parcelas,
            }
            for x in formas
        ],
    }


@router.get("/empresas")
async def listar_empresas_financeiras(session: SessionDep, usuario: ViewDep) -> dict:
    empresas = (
        (
            await session.execute(
                select(EmpresaCRM)
                .where(EmpresaCRM.organizacao_id == usuario.organizacao_id)
                .order_by(EmpresaCRM.nome)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    return {
        "empresas": [
            {
                "id": item.id,
                "nome": item.nome,
                "documento": item.documento,
                "email": item.email,
                "telefone": item.telefone,
            }
            for item in empresas
        ]
    }


@router.post("/empresas", status_code=201)
async def criar_empresa_financeira(dados: EmpresaFinanceiraCreate, session: SessionDep, usuario: ManageDep) -> dict:
    nome = dados.nome.strip()
    normalizado = normalizar_empresa(nome)
    existente = (
        await session.execute(
            select(EmpresaCRM).where(
                EmpresaCRM.organizacao_id == usuario.organizacao_id,
                EmpresaCRM.nome_normalizado == normalizado,
            )
        )
    ).scalar_one_or_none()
    if existente:
        return {"id": existente.id, "nome": existente.nome, "idempotente": True}
    empresa = EmpresaCRM(
        organizacao_id=usuario.organizacao_id,
        nome=nome,
        nome_normalizado=normalizado,
        documento=dados.documento,
        email=dados.email,
        telefone=dados.telefone,
        segmento=dados.segmento,
        observacoes=dados.observacoes,
    )
    session.add(empresa)
    await session.commit()
    return {"id": empresa.id, "nome": empresa.nome, "idempotente": False}


@router.get("/formas-pagamento")
async def listar_formas_pagamento(session: SessionDep, usuario: ViewDep) -> dict:
    formas = (
        (
            await session.execute(
                select(FormaPagamentoFinanceira)
                .where(FormaPagamentoFinanceira.organizacao_id == usuario.organizacao_id)
                .order_by(FormaPagamentoFinanceira.ativo.desc(), FormaPagamentoFinanceira.nome)
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": x.id,
                "nome": x.nome,
                "tipo": x.tipo,
                "permite_parcelamento": x.permite_parcelamento,
                "maximo_parcelas": x.maximo_parcelas,
                "ativo": x.ativo,
                "criado_em": x.criado_em,
            }
            for x in formas
        ]
    }


async def _salvar_forma_pagamento(
    dados: FormaPagamentoCreate,
    request: Request,
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    forma: FormaPagamentoFinanceira | None = None,
) -> FormaPagamentoFinanceira:
    nome = dados.nome.strip()
    duplicada = (
        await session.execute(
            select(FormaPagamentoFinanceira.id).where(
                FormaPagamentoFinanceira.organizacao_id == usuario.organizacao_id,
                func.lower(FormaPagamentoFinanceira.nome) == nome.lower(),
                *([FormaPagamentoFinanceira.id != forma.id] if forma else []),
            )
        )
    ).scalar_one_or_none()
    if duplicada:
        raise HTTPException(409, "Forma de pagamento já cadastrada")
    maximo = dados.maximo_parcelas if dados.permite_parcelamento else 1
    if forma is None:
        forma = FormaPagamentoFinanceira(organizacao_id=usuario.organizacao_id)
        session.add(forma)
    forma.nome = nome
    forma.tipo = dados.tipo
    forma.permite_parcelamento = dados.permite_parcelamento
    forma.maximo_parcelas = maximo
    forma.ativo = dados.ativo
    await session.flush()
    _auditar(
        session,
        request,
        usuario,
        "salvar_forma_pgto",
        f"forma-pagamento:{forma.id}",
        {"nome": nome, "tipo": dados.tipo, "maximo_parcelas": maximo, "ativo": dados.ativo},
    )
    await session.commit()
    return forma


@router.post("/formas-pagamento", status_code=201)
async def criar_forma_pagamento(
    dados: FormaPagamentoCreate, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    forma = await _salvar_forma_pagamento(dados, request, session, usuario)
    return {"id": forma.id, "status": "criada"}


@router.put("/formas-pagamento/{forma_id}")
async def editar_forma_pagamento(
    forma_id: int,
    dados: FormaPagamentoCreate,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    forma = await _forma_pagamento(session, usuario, forma_id, exigir_ativa=False)
    await _salvar_forma_pagamento(dados, request, session, usuario, forma)
    return {"id": forma.id, "status": "atualizada"}


# --- Tabela de retribuições do INPI (referência global) --------------------


class RetribuicaoInput(BaseModel):
    descricao: str = Field(min_length=2, max_length=200)
    codigo: str | None = Field(default=None, max_length=10)
    valor_normal: Decimal | None = Field(default=None, ge=0)
    valor_reduzido: Decimal | None = Field(default=None, ge=0)
    fase_sugerida: str | None = Field(default=None, max_length=30)
    ativo: bool = True
    observacoes: str | None = Field(default=None, max_length=2000)


class RetribuicaoCreate(RetribuicaoInput):
    servico: str = Field(min_length=2, max_length=60)


def _retribuicao_dict(x: RetribuicaoInpi) -> dict:
    return {
        "id": x.id,
        "servico": x.servico,
        "descricao": x.descricao,
        "grupo": x.grupo,
        "codigo": x.codigo,
        "valor_normal": x.valor_normal,
        "valor_reduzido": x.valor_reduzido,
        "fase_sugerida": x.fase_sugerida,
        "confirmado": x.confirmado,
        "ativo": x.ativo,
        "observacoes": x.observacoes,
        "atualizado_em": x.atualizado_em,
    }


@router.get("/retribuicoes")
async def listar_retribuicoes(session: SessionDep, usuario: ViewDep) -> dict:
    itens = (
        (await session.execute(select(RetribuicaoInpi).order_by(RetribuicaoInpi.ordem, RetribuicaoInpi.descricao)))
        .scalars()
        .all()
    )
    return {
        "itens": [_retribuicao_dict(x) for x in itens],
        "pendentes": sum(1 for x in itens if not x.confirmado),
    }


@router.post("/retribuicoes", status_code=201)
async def criar_retribuicao(
    dados: RetribuicaoCreate, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    servico = dados.servico.strip()
    duplicada = (
        await session.execute(select(RetribuicaoInpi.id).where(func.lower(RetribuicaoInpi.servico) == servico.lower()))
    ).scalar_one_or_none()
    if duplicada:
        raise HTTPException(409, "Serviço já cadastrado")
    ordem = (await session.execute(select(func.coalesce(func.max(RetribuicaoInpi.ordem), 0)))).scalar_one() + 1
    item = RetribuicaoInpi(
        servico=servico,
        descricao=dados.descricao.strip(),
        codigo=(dados.codigo or None),
        valor_normal=dados.valor_normal,
        valor_reduzido=dados.valor_reduzido,
        fase_sugerida=(dados.fase_sugerida or None),
        ativo=dados.ativo,
        observacoes=dados.observacoes,
        confirmado=True,
        ordem=ordem,
    )
    session.add(item)
    await session.flush()
    _auditar(
        session,
        request,
        usuario,
        "criar_retribuicao",
        f"retribuicao:{item.id}",
        {"servico": servico},
    )
    await session.commit()
    return {"id": item.id, "status": "criada"}


@router.put("/retribuicoes/{item_id}")
async def editar_retribuicao(
    item_id: int, dados: RetribuicaoInput, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    item = (await session.execute(select(RetribuicaoInpi).where(RetribuicaoInpi.id == item_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Retribuição não encontrada")
    item.descricao = dados.descricao.strip()
    item.codigo = dados.codigo or None
    item.valor_normal = dados.valor_normal
    item.valor_reduzido = dados.valor_reduzido
    item.fase_sugerida = dados.fase_sugerida or None
    item.ativo = dados.ativo
    item.observacoes = dados.observacoes
    item.confirmado = True
    _auditar(
        session,
        request,
        usuario,
        "editar_retribuicao",
        f"retribuicao:{item.id}",
        {"valor_normal": str(dados.valor_normal), "valor_reduzido": str(dados.valor_reduzido)},
    )
    await session.commit()
    return {"id": item.id, "status": "atualizada"}


@router.get("/categorias")
async def listar_categorias(session: SessionDep, usuario: ViewDep) -> dict:
    categorias = (
        (
            await session.execute(
                select(CategoriaFinanceira)
                .where(CategoriaFinanceira.organizacao_id == usuario.organizacao_id)
                .order_by(CategoriaFinanceira.ativo.desc(), CategoriaFinanceira.nome)
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": x.id,
                "nome": x.nome,
                "tipo": x.tipo,
                "categoria_pai_id": x.categoria_pai_id,
                "ativo": x.ativo,
                "criado_em": x.criado_em,
            }
            for x in categorias
        ]
    }


async def _categoria_pai(
    session: AsyncSession, usuario: UsuarioAutenticado, categoria_pai_id: int | None
) -> CategoriaFinanceira | None:
    if not categoria_pai_id:
        return None
    pai = (
        await session.execute(
            select(CategoriaFinanceira).where(
                CategoriaFinanceira.id == categoria_pai_id,
                CategoriaFinanceira.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if pai is None:
        raise HTTPException(404, "Categoria pai não encontrada")
    # Pedido do usuário (22/09/2026): profundidade limitada a 1 nível --
    # uma subcategoria não pode, por sua vez, ter subcategorias.
    if pai.categoria_pai_id is not None:
        raise HTTPException(422, "Não é possível criar uma subcategoria de outra subcategoria")
    return pai


async def _salvar_categoria(
    dados: CategoriaCreate,
    request: Request,
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    categoria: CategoriaFinanceira | None = None,
) -> CategoriaFinanceira:
    nome = dados.nome.strip()
    pai = await _categoria_pai(session, usuario, dados.categoria_pai_id)
    if pai is not None and categoria is not None and pai.id == categoria.id:
        raise HTTPException(422, "Uma categoria não pode ser subcategoria de si mesma")
    if pai is not None and categoria is not None:
        tem_filhas = (
            await session.execute(
                select(exists().where(CategoriaFinanceira.categoria_pai_id == categoria.id))
            )
        ).scalar_one()
        if tem_filhas:
            raise HTTPException(422, "Esta categoria já tem subcategorias -- não pode virar subcategoria de outra")
    # Subcategoria sempre herda o tipo do pai, pra não ficar incoerente com
    # ele em telas que filtram categoria por tipo (pagar/receber).
    tipo = pai.tipo if pai else dados.tipo
    duplicada = (
        await session.execute(
            select(CategoriaFinanceira.id).where(
                CategoriaFinanceira.organizacao_id == usuario.organizacao_id,
                func.lower(CategoriaFinanceira.nome) == nome.lower(),
                CategoriaFinanceira.tipo == tipo,
                *([CategoriaFinanceira.id != categoria.id] if categoria else []),
            )
        )
    ).scalar_one_or_none()
    if duplicada:
        raise HTTPException(409, "Categoria já cadastrada")
    if categoria is not None and not dados.ativo:
        tem_filhas_ativas = (
            await session.execute(
                select(
                    exists().where(
                        CategoriaFinanceira.categoria_pai_id == categoria.id,
                        CategoriaFinanceira.ativo.is_(True),
                    )
                )
            )
        ).scalar_one()
        if tem_filhas_ativas:
            raise HTTPException(409, "Desative as subcategorias antes de desativar esta categoria")
    if categoria is None:
        categoria = CategoriaFinanceira(organizacao_id=usuario.organizacao_id)
        session.add(categoria)
    categoria.nome = nome
    categoria.tipo = tipo
    categoria.categoria_pai_id = pai.id if pai else None
    categoria.ativo = dados.ativo
    await session.flush()
    _auditar(
        session,
        request,
        usuario,
        "salvar_categoria",
        f"categoria-financeira:{categoria.id}",
        {"nome": nome, "tipo": tipo, "categoria_pai_id": categoria.categoria_pai_id, "ativo": dados.ativo},
    )
    await session.commit()
    return categoria


@router.post("/categorias", status_code=201)
async def criar_categoria(dados: CategoriaCreate, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    categoria = await _salvar_categoria(dados, request, session, usuario)
    return {
        "id": categoria.id,
        "nome": categoria.nome,
        "tipo": categoria.tipo,
        "categoria_pai_id": categoria.categoria_pai_id,
    }


@router.put("/categorias/{categoria_id}")
async def editar_categoria(
    categoria_id: int, dados: CategoriaCreate, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    categoria = (
        await session.execute(
            select(CategoriaFinanceira).where(
                CategoriaFinanceira.id == categoria_id,
                CategoriaFinanceira.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if categoria is None:
        raise HTTPException(404, "Categoria não encontrada")
    await _salvar_categoria(dados, request, session, usuario, categoria)
    return {"id": categoria.id, "status": "atualizada"}


# --- Achado FASE7-13/14 da auditoria (04/09/2026): plano de contas gerencial
# e DRE -- ver app/plano_contas.py. ---


@router.get("/plano-contas")
async def listar_plano_contas(session: SessionDep, usuario: ViewDep) -> dict:
    contas = (
        (
            await session.execute(
                select(PlanoContas)
                .where(PlanoContas.organizacao_id == usuario.organizacao_id, PlanoContas.ativo.is_(True))
                .order_by(PlanoContas.codigo)
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": c.id,
                "codigo": c.codigo,
                "nome": c.nome,
                "natureza": c.natureza,
                "grupo_dre": c.grupo_dre,
                "conta_pai_id": c.conta_pai_id,
            }
            for c in contas
        ]
    }


@router.post("/plano-contas", status_code=201)
async def criar_conta_plano(dados: PlanoContasCreate, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    existente = (
        await session.execute(
            select(PlanoContas.id).where(
                PlanoContas.organizacao_id == usuario.organizacao_id, PlanoContas.codigo == dados.codigo
            )
        )
    ).scalar_one_or_none()
    if existente:
        raise HTTPException(409, "Já existe uma conta com esse código")
    if dados.conta_pai_id:
        pai = (
            await session.execute(
                select(PlanoContas.id).where(
                    PlanoContas.id == dados.conta_pai_id, PlanoContas.organizacao_id == usuario.organizacao_id
                )
            )
        ).scalar_one_or_none()
        if not pai:
            raise HTTPException(404, "Conta pai não encontrada")
    conta = PlanoContas(
        organizacao_id=usuario.organizacao_id,
        conta_pai_id=dados.conta_pai_id,
        codigo=dados.codigo.strip(),
        nome=dados.nome.strip(),
        natureza=dados.natureza,
        grupo_dre=dados.grupo_dre,
    )
    session.add(conta)
    await session.flush()
    _auditar(session, request, usuario, "criar_conta_pc", f"plano-contas:{conta.id}", {"codigo": conta.codigo})
    await session.commit()
    return {"id": conta.id, "codigo": conta.codigo}


@router.post("/plano-contas/seed-padrao", status_code=201)
async def semear_plano_contas_padrao(request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    """Cria o conjunto padrão de contas (app.plano_contas.CONTAS_PADRAO) --
    idempotente: só cria os códigos que ainda não existem para a organização,
    nunca sobrescreve o que já foi customizado."""
    existentes = set(
        (
            await session.execute(
                select(PlanoContas.codigo).where(PlanoContas.organizacao_id == usuario.organizacao_id)
            )
        )
        .scalars()
        .all()
    )
    criadas = 0
    for codigo, nome, natureza, grupo_dre in CONTAS_PADRAO:
        if codigo in existentes:
            continue
        session.add(
            PlanoContas(
                organizacao_id=usuario.organizacao_id,
                codigo=codigo,
                nome=nome,
                natureza=natureza,
                grupo_dre=grupo_dre,
            )
        )
        criadas += 1
    _auditar(session, request, usuario, "seed_plano_contas", "plano-contas:seed", {"criadas": criadas})
    await session.commit()
    return {"criadas": criadas}


@router.get("/dre")
async def obter_dre(
    session: SessionDep,
    usuario: ViewDep,
    competencia_de: Annotated[date, Query()],
    competencia_ate: Annotated[date, Query()],
) -> dict:
    if competencia_ate < competencia_de:
        raise HTTPException(422, "competencia_ate não pode ser anterior a competencia_de")
    linhas = (
        await session.execute(
            select(
                PlanoContas.grupo_dre,
                LancamentoFinanceiro.tipo,
                func.coalesce(func.sum(LancamentoFinanceiro.valor_total), 0),
            )
            .join(PlanoContas, PlanoContas.id == LancamentoFinanceiro.conta_contabil_id)
            .where(
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.status != "cancelado",
                LancamentoFinanceiro.competencia >= competencia_de,
                LancamentoFinanceiro.competencia <= competencia_ate,
            )
            .group_by(PlanoContas.grupo_dre, LancamentoFinanceiro.tipo)
        )
    ).all()
    totais_por_grupo: dict[str, tuple[Decimal, Decimal]] = {}
    for grupo, tipo, total in linhas:
        receitas, despesas = totais_por_grupo.get(grupo, (Decimal("0"), Decimal("0")))
        if tipo == "receber":
            receitas += Decimal(total)
        else:
            despesas += Decimal(total)
        totais_por_grupo[grupo] = (receitas, despesas)

    valor_sem_classificacao = (
        await session.execute(
            select(func.coalesce(func.sum(LancamentoFinanceiro.valor_total), 0)).where(
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.status != "cancelado",
                LancamentoFinanceiro.conta_contabil_id.is_(None),
                LancamentoFinanceiro.competencia >= competencia_de,
                LancamentoFinanceiro.competencia <= competencia_ate,
            )
        )
    ).scalar_one()

    resultado = montar_dre(
        competencia_de=competencia_de,
        competencia_ate=competencia_ate,
        totais_por_grupo=totais_por_grupo,
        valor_sem_classificacao=Decimal(valor_sem_classificacao),
    )
    return {
        "competencia_de": resultado.competencia_de,
        "competencia_ate": resultado.competencia_ate,
        "linhas": [
            {"grupo": linha.grupo, "receitas": str(linha.receitas), "despesas": str(linha.despesas), "saldo": str(linha.saldo)}
            for linha in resultado.linhas
        ],
        "receita_bruta": str(resultado.receita_bruta),
        "receita_liquida": str(resultado.receita_liquida),
        "lucro_bruto": str(resultado.lucro_bruto),
        "resultado_operacional": str(resultado.resultado_operacional),
        "resultado_liquido": str(resultado.resultado_liquido),
        "valor_sem_classificacao": str(resultado.valor_sem_classificacao),
    }


@router.post("/lancamentos", status_code=201)
async def criar_lancamento(dados: LancamentoCreate, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    await _validar_referencias(
        session, usuario, dados.tipo, dados.empresa_id, dados.categoria_id, dados.conta_contabil_id
    )
    forma = await _forma_pagamento(session, usuario, dados.forma_pagamento_id)
    _validar_parcelamento(forma, dados.quantidade_parcelas)
    lancamento = LancamentoFinanceiro(
        organizacao_id=usuario.organizacao_id,
        empresa_id=dados.empresa_id,
        categoria_id=dados.categoria_id,
        conta_contabil_id=dados.conta_contabil_id,
        forma_pagamento_id=dados.forma_pagamento_id,
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
    _registrar_historico(
        session,
        usuario,
        lancamento.id,
        "criacao",
        "Lançamento criado",
        {
            "tipo": dados.tipo,
            "valor": str(dados.valor_total),
            "parcelas": dados.quantidade_parcelas,
        },
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


@router.put("/lancamentos/{lancamento_id}")
async def editar_lancamento(
    lancamento_id: int,
    dados: LancamentoUpdate,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    lancamento = (
        await session.execute(
            select(LancamentoFinanceiro)
            .options(selectinload(LancamentoFinanceiro.parcelas))
            .where(
                LancamentoFinanceiro.id == lancamento_id,
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
            )
            .with_for_update(of=LancamentoFinanceiro)
        )
    ).scalar_one_or_none()
    if not lancamento:
        raise HTTPException(404, "Lançamento não encontrado")
    if lancamento.status == "cancelado":
        raise HTTPException(409, "Lançamento cancelado não pode ser alterado")

    await _validar_referencias(
        session, usuario, lancamento.tipo, dados.empresa_id, dados.categoria_id, dados.conta_contabil_id
    )
    forma = await _forma_pagamento(session, usuario, dados.forma_pagamento_id)
    _validar_parcelamento(forma, dados.quantidade_parcelas)
    parcelas_pagas = any(parcela.status == "paga" for parcela in lancamento.parcelas)
    estrutura_alterada = (
        Decimal(lancamento.valor_total) != dados.valor_total
        or len(lancamento.parcelas) != dados.quantidade_parcelas
        or not lancamento.parcelas
        or lancamento.parcelas[0].vencimento != dados.primeiro_vencimento
    )
    if parcelas_pagas and estrutura_alterada:
        raise HTTPException(
            409,
            "Valor, quantidade e vencimentos não podem mudar após uma baixa; estorne primeiro",
        )

    anterior = {
        "descricao": lancamento.descricao,
        "valor_total": str(lancamento.valor_total),
        "parcelas": len(lancamento.parcelas),
    }
    lancamento.descricao = dados.descricao.strip()
    lancamento.documento = (dados.documento or "").strip() or None
    lancamento.competencia = dados.competencia
    lancamento.empresa_id = dados.empresa_id
    lancamento.categoria_id = dados.categoria_id
    lancamento.conta_contabil_id = dados.conta_contabil_id
    lancamento.forma_pagamento_id = dados.forma_pagamento_id
    lancamento.observacoes = (dados.observacoes or "").strip() or None
    lancamento.atualizado_em = datetime.now(UTC)

    if estrutura_alterada:
        lancamento.valor_total = dados.valor_total
        parcelas_atuais = list(lancamento.parcelas)
        novos_valores = _parcelar(dados.valor_total, dados.quantidade_parcelas)
        for indice, valor in enumerate(novos_valores):
            if indice < len(parcelas_atuais):
                parcela = parcelas_atuais[indice]
                parcela.numero = indice + 1
                parcela.vencimento = _mes_seguinte(dados.primeiro_vencimento, indice)
                parcela.valor = valor
                parcela.valor_pago = Decimal(0)
                parcela.status = "aberta"
                parcela.pago_em = None
                parcela.forma_pagamento = None
                parcela.observacoes_baixa = None
            else:
                lancamento.parcelas.append(
                    ParcelaFinanceira(
                        organizacao_id=usuario.organizacao_id,
                        numero=indice + 1,
                        vencimento=_mes_seguinte(dados.primeiro_vencimento, indice),
                        valor=valor,
                        valor_pago=Decimal(0),
                    )
                )
        for parcela in parcelas_atuais[len(novos_valores) :]:
            lancamento.parcelas.remove(parcela)
            await session.delete(parcela)
        lancamento.status = "aberto"

    _registrar_historico(
        session,
        usuario,
        lancamento.id,
        "edicao",
        "Lançamento atualizado",
        {
            "anterior": anterior,
            "valor_total": str(dados.valor_total),
            "parcelas": dados.quantidade_parcelas,
        },
    )
    _auditar(
        session,
        request,
        usuario,
        "editar_lancamento",
        f"lancamento-financeiro:{lancamento.id}",
        {
            "anterior": anterior,
            "novo": {
                "descricao": lancamento.descricao,
                "valor_total": str(dados.valor_total),
                "parcelas": dados.quantidade_parcelas,
            },
        },
    )
    await session.commit()
    return {"id": lancamento.id, "status": "atualizado"}


async def _parcela(session: AsyncSession, usuario: UsuarioAutenticado, parcela_id: int) -> ParcelaFinanceira:
    parcela = (
        await session.execute(
            select(ParcelaFinanceira)
            .join(
                LancamentoFinanceiro,
                LancamentoFinanceiro.id == ParcelaFinanceira.lancamento_id,
            )
            .options(selectinload(ParcelaFinanceira.lancamento).selectinload(LancamentoFinanceiro.parcelas))
            .where(
                ParcelaFinanceira.id == parcela_id,
                ParcelaFinanceira.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
            )
            .with_for_update(of=(LancamentoFinanceiro, ParcelaFinanceira))
        )
    ).scalar_one_or_none()
    if not parcela:
        raise HTTPException(404, "Parcela não encontrada")
    return parcela


async def _gerar_comissao_se_aplicavel(session: AsyncSession, parcela: ParcelaFinanceira) -> None:
    """Achado FASE7-7 da auditoria (04/09/2026): gera a comissão do
    responsável pelo lead quando uma parcela de receita é baixada -- silencioso
    (não gera nada) se o lançamento não for "receber", não tiver lead
    vinculado, o lead não tiver responsável, ou o responsável não tiver
    percentual_comissao configurado (retrocompatível: sem opt-in, nada muda)."""
    lancamento = parcela.lancamento
    if lancamento.tipo != "receber" or not lancamento.lead_id:
        return
    lead = await session.get(Lead, lancamento.lead_id)
    if lead is None or not lead.responsavel_id:
        return
    operador = await session.get(UsuarioOperacoes, lead.responsavel_id)
    if operador is None or not operador.percentual_comissao:
        return
    valor_comissao = (parcela.valor_pago * operador.percentual_comissao / Decimal(100)).quantize(Decimal("0.01"))
    session.add(
        ComissaoFinanceira(
            organizacao_id=lancamento.organizacao_id,
            usuario_id=operador.id,
            lancamento_id=lancamento.id,
            parcela_id=parcela.id,
            valor_base=parcela.valor_pago,
            percentual=operador.percentual_comissao,
            valor_comissao=valor_comissao,
        )
    )


async def _cancelar_comissao_da_parcela(session: AsyncSession, parcela_id: int) -> None:
    """Achado FASE7-7/4 da auditoria: estornar a baixa que gerou uma comissão
    cancela a comissão (nunca deleta -- mantém rastro de que existiu e foi
    cancelada). Se a comissão já tinha sido paga, o cancelamento fica
    registrado mesmo assim -- reconciliar o valor já pago é decisão humana,
    não automática."""
    comissao = (
        await session.execute(
            select(ComissaoFinanceira).where(
                ComissaoFinanceira.parcela_id == parcela_id, ComissaoFinanceira.status != "cancelada"
            )
        )
    ).scalar_one_or_none()
    if comissao is not None:
        comissao.status = "cancelada"


@router.post("/parcelas/{parcela_id}/baixar")
async def baixar(
    parcela_id: int, dados: BaixaCreate, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    parcela = await _parcela(session, usuario, parcela_id)
    if parcela.lancamento.status == "cancelado" or parcela.status == "paga":
        raise HTTPException(409, "Parcela não pode ser baixada")
    if dados.valor_pago != Decimal(parcela.valor):
        raise HTTPException(422, "Neste MVP, a baixa deve usar o valor integral da parcela")
    forma = await _forma_pagamento(session, usuario, dados.forma_pagamento_id)
    parcela.valor_pago, parcela.pago_em, parcela.forma_pagamento = (
        dados.valor_pago,
        dados.pago_em,
        forma.nome,
    )
    parcela.forma_pagamento_id = forma.id
    parcela.observacoes_baixa, parcela.status = (dados.observacoes or "").strip() or None, "paga"
    await _atualizar_status(parcela.lancamento)
    await _gerar_comissao_se_aplicavel(session, parcela)
    if parcela.lancamento.proposta_id:
        await sincronizar_pagamento_proposta_por_id(session, usuario.organizacao_id, parcela.lancamento.proposta_id)
    _auditar(
        session,
        request,
        usuario,
        "baixar_parcela",
        f"parcela-financeira:{parcela.id}",
        {"valor": str(dados.valor_pago), "data": str(dados.pago_em)},
    )
    _registrar_historico(
        session,
        usuario,
        parcela.lancamento_id,
        "baixa",
        f"Baixa da {parcela.numero}ª parcela registrada",
        {
            "valor": str(dados.valor_pago),
            "data": str(dados.pago_em),
            "forma_pagamento": forma.nome,
            "observacoes": dados.observacoes,
        },
        parcela.id,
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
    forma_anterior = parcela.forma_pagamento
    parcela.valor_pago, parcela.pago_em, parcela.forma_pagamento = Decimal(0), None, None
    parcela.forma_pagamento_id = None
    parcela.observacoes_baixa, parcela.status = None, "aberta"
    await _atualizar_status(parcela.lancamento)
    await _cancelar_comissao_da_parcela(session, parcela.id)
    if parcela.lancamento.proposta_id:
        await sincronizar_pagamento_proposta_por_id(session, usuario.organizacao_id, parcela.lancamento.proposta_id)
    _auditar(
        session,
        request,
        usuario,
        "estornar_baixa",
        f"parcela-financeira:{parcela.id}",
        {"valor_anterior": anterior, "motivo": dados.motivo},
    )
    _registrar_historico(
        session,
        usuario,
        parcela.lancamento_id,
        "estorno",
        f"Baixa da {parcela.numero}ª parcela estornada",
        {
            "valor_anterior": anterior,
            "forma_pagamento": forma_anterior,
            "motivo": dados.motivo.strip(),
        },
        parcela.id,
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
            .with_for_update(of=LancamentoFinanceiro)
        )
    ).scalar_one_or_none()
    if not lancamento:
        raise HTTPException(404, "Lançamento não encontrado")
    if lancamento.status == "cancelado" or any(p.status == "paga" for p in lancamento.parcelas):
        raise HTTPException(409, "Estorne as baixas antes de cancelar")
    lancamento.status, lancamento.cancelado_em = "cancelado", datetime.now(UTC)
    lancamento.cancelado_por, lancamento.cancelamento_motivo = usuario.ator, dados.motivo.strip()
    if lancamento.proposta_id:
        await sincronizar_pagamento_proposta_por_id(session, usuario.organizacao_id, lancamento.proposta_id)
    _auditar(
        session,
        request,
        usuario,
        "cancelar_lancamento",
        f"lancamento-financeiro:{lancamento.id}",
        {"motivo": dados.motivo},
    )
    _registrar_historico(
        session,
        usuario,
        lancamento.id,
        "cancelamento",
        "Lançamento cancelado",
        {"motivo": dados.motivo.strip()},
    )
    await session.commit()
    return {"status": "cancelado"}


@router.get("/lancamentos/{lancamento_id}/historico")
async def historico_lancamento(lancamento_id: int, session: SessionDep, usuario: ViewDep) -> dict:
    existe = (
        await session.execute(
            select(LancamentoFinanceiro.id).where(
                LancamentoFinanceiro.id == lancamento_id,
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if not existe:
        raise HTTPException(404, "Lançamento não encontrado")
    eventos = (
        (
            await session.execute(
                select(HistoricoFinanceiro)
                .where(
                    HistoricoFinanceiro.lancamento_id == lancamento_id,
                    HistoricoFinanceiro.organizacao_id == usuario.organizacao_id,
                )
                .order_by(HistoricoFinanceiro.criado_em.desc(), HistoricoFinanceiro.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": x.id,
                "acao": x.acao,
                "descricao": x.descricao,
                "ator": x.ator,
                "parcela_id": x.parcela_id,
                "detalhes": x.detalhes,
                "criado_em": x.criado_em,
            }
            for x in eventos
        ]
    }


@router.get("/logs")
async def listar_logs_financeiros(
    session: SessionDep,
    usuario: LogDep,
    acao: Literal["baixa", "estorno", "cancelamento"] | None = None,
    inicio: date | None = None,
    fim: date | None = None,
    busca: Annotated[str | None, Query(max_length=120)] = None,
    limite: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    filtros = [HistoricoFinanceiro.organizacao_id == usuario.organizacao_id]
    if acao:
        filtros.append(HistoricoFinanceiro.acao == acao)
    if inicio:
        filtros.append(func.date(HistoricoFinanceiro.criado_em) >= inicio)
    if fim:
        filtros.append(func.date(HistoricoFinanceiro.criado_em) <= fim)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(
            or_(
                LancamentoFinanceiro.descricao.ilike(termo),
                LancamentoFinanceiro.documento.ilike(termo),
                HistoricoFinanceiro.ator.ilike(termo),
                HistoricoFinanceiro.descricao.ilike(termo),
            )
        )
    linhas = (
        await session.execute(
            select(HistoricoFinanceiro, LancamentoFinanceiro)
            .join(
                LancamentoFinanceiro,
                LancamentoFinanceiro.id == HistoricoFinanceiro.lancamento_id,
            )
            .where(*filtros)
            .order_by(HistoricoFinanceiro.criado_em.desc(), HistoricoFinanceiro.id.desc())
            .limit(limite)
        )
    ).all()
    return {
        "itens": [
            {
                "id": evento.id,
                "acao": evento.acao,
                "descricao": evento.descricao,
                "ator": evento.ator,
                "detalhes": evento.detalhes,
                "criado_em": evento.criado_em,
                "lancamento_id": lancamento.id,
                "lancamento": lancamento.descricao,
                "documento": lancamento.documento,
                "tipo": lancamento.tipo,
                "parcela_id": evento.parcela_id,
            }
            for evento, lancamento in linhas
        ],
        "total": len(linhas),
        "limite": limite,
    }


@router.get("/exportar.csv")
async def exportar(
    session: SessionDep,
    usuario: ExportDep,
    tipo: Literal["pagar", "receber"] | None = None,
) -> StreamingResponse:
    filtros = [LancamentoFinanceiro.organizacao_id == usuario.organizacao_id]
    if tipo:
        filtros.append(LancamentoFinanceiro.tipo == tipo)
    itens = (
        (
            await session.execute(
                select(LancamentoFinanceiro)
                .options(
                    selectinload(LancamentoFinanceiro.parcelas),
                    selectinload(LancamentoFinanceiro.empresa_registro),
                    selectinload(LancamentoFinanceiro.categoria),
                )
                .where(*filtros)
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


# --- Achado FASE7-8/9 da auditoria (04/09/2026): custo por processo e
# lucratividade por cliente/carteira -- reaproveita LancamentoFinanceiro
# (j\u00e1 tem processo_id/empresa_id) e CustoJuridico (s\u00f3 tem processo_id, n\u00e3o
# gera LancamentoFinanceiro -- por isso entra separado na soma de custo).
# ---


def _margem(receita: Decimal, custo: Decimal) -> tuple[Decimal, float | None]:
    margem = receita - custo
    margem_pct = float(margem / receita) if receita else None
    return margem, margem_pct


@router.get("/custo-processo/{processo_id}")
async def obter_custo_processo(processo_id: int, session: SessionDep, usuario: ViewDep) -> dict:
    processo = (
        await session.execute(select(Processo).where(Processo.id == processo_id))
    ).scalar_one_or_none()
    if processo is None:
        raise HTTPException(404, "Processo n\u00e3o encontrado")

    receita = (
        await session.execute(
            select(func.coalesce(func.sum(LancamentoFinanceiro.valor_total), 0)).where(
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.processo_id == processo_id,
                LancamentoFinanceiro.tipo == "receber",
                LancamentoFinanceiro.status != "cancelado",
            )
        )
    ).scalar_one()
    custo_lancamentos = (
        await session.execute(
            select(func.coalesce(func.sum(LancamentoFinanceiro.valor_total), 0)).where(
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.processo_id == processo_id,
                LancamentoFinanceiro.tipo == "pagar",
                LancamentoFinanceiro.status != "cancelado",
            )
        )
    ).scalar_one()
    custo_juridico = (
        await session.execute(
            select(func.coalesce(func.sum(CustoJuridico.valor), 0)).where(
                CustoJuridico.organizacao_id == usuario.organizacao_id,
                CustoJuridico.processo_id == processo_id,
            )
        )
    ).scalar_one()

    receita_d, custo_lanc_d, custo_jur_d = Decimal(receita), Decimal(custo_lancamentos), Decimal(custo_juridico)
    custo_total = custo_lanc_d + custo_jur_d
    margem, margem_pct = _margem(receita_d, custo_total)
    return {
        "processo_id": processo_id,
        "numero_processo": processo.numero,
        "receita": str(receita_d),
        "custo_lancamentos": str(custo_lanc_d),
        "custo_juridico": str(custo_jur_d),
        "custo_total": str(custo_total),
        "margem": str(margem),
        "margem_pct": margem_pct,
    }


@router.get("/lucratividade/clientes")
async def obter_lucratividade_clientes(
    session: SessionDep,
    usuario: ViewDep,
    competencia_de: Annotated[date, Query()],
    competencia_ate: Annotated[date, Query()],
) -> dict:
    if competencia_ate < competencia_de:
        raise HTTPException(422, "competencia_ate n\u00e3o pode ser anterior a competencia_de")
    linhas = (
        await session.execute(
            select(
                LancamentoFinanceiro.empresa_id,
                EmpresaCRM.nome,
                LancamentoFinanceiro.tipo,
                func.coalesce(func.sum(LancamentoFinanceiro.valor_total), 0),
            )
            .outerjoin(EmpresaCRM, EmpresaCRM.id == LancamentoFinanceiro.empresa_id)
            .where(
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.status != "cancelado",
                LancamentoFinanceiro.competencia >= competencia_de,
                LancamentoFinanceiro.competencia <= competencia_ate,
            )
            .group_by(LancamentoFinanceiro.empresa_id, EmpresaCRM.nome, LancamentoFinanceiro.tipo)
        )
    ).all()

    por_cliente: dict[int | None, dict] = {}
    for empresa_id, nome, tipo, total in linhas:
        entrada = por_cliente.setdefault(
            empresa_id, {"empresa_id": empresa_id, "nome": nome or "(sem cliente vinculado)", "receita": Decimal("0"), "custo": Decimal("0")}
        )
        if tipo == "receber":
            entrada["receita"] += Decimal(total)
        else:
            entrada["custo"] += Decimal(total)

    clientes = []
    receita_carteira = Decimal("0")
    custo_carteira = Decimal("0")
    for entrada in por_cliente.values():
        margem, margem_pct = _margem(entrada["receita"], entrada["custo"])
        receita_carteira += entrada["receita"]
        custo_carteira += entrada["custo"]
        clientes.append(
            {
                "empresa_id": entrada["empresa_id"],
                "nome": entrada["nome"],
                "receita": str(entrada["receita"]),
                "custo": str(entrada["custo"]),
                "margem": str(margem),
                "margem_pct": margem_pct,
            }
        )
    clientes.sort(key=lambda item: Decimal(item["margem"]), reverse=True)
    margem_carteira, margem_pct_carteira = _margem(receita_carteira, custo_carteira)

    return {
        "competencia_de": competencia_de,
        "competencia_ate": competencia_ate,
        "clientes": clientes,
        "carteira": {
            "receita": str(receita_carteira),
            "custo": str(custo_carteira),
            "margem": str(margem_carteira),
            "margem_pct": margem_pct_carteira,
        },
    }


# --- Achado FASE7-7 da auditoria (04/09/2026): comissão de operador. ---


@router.get("/comissoes")
async def listar_comissoes(
    session: SessionDep,
    usuario: ViewDep,
    usuario_id: Annotated[int | None, Query()] = None,
    status_comissao: Annotated[str | None, Query(alias="status")] = None,
) -> dict:
    filtros = [ComissaoFinanceira.organizacao_id == usuario.organizacao_id]
    if usuario_id:
        filtros.append(ComissaoFinanceira.usuario_id == usuario_id)
    if status_comissao:
        filtros.append(ComissaoFinanceira.status == status_comissao)
    itens = (
        (
            await session.execute(
                select(ComissaoFinanceira).where(*filtros).order_by(ComissaoFinanceira.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {
                "id": c.id,
                "usuario_id": c.usuario_id,
                "usuario_nome": c.usuario.nome if c.usuario else None,
                "lancamento_id": c.lancamento_id,
                "parcela_id": c.parcela_id,
                "valor_base": str(c.valor_base),
                "percentual": str(c.percentual),
                "valor_comissao": str(c.valor_comissao),
                "status": c.status,
                "pago_em": c.pago_em,
            }
            for c in itens
        ]
    }


@router.post("/comissoes/{comissao_id}/pagar")
async def pagar_comissao(comissao_id: int, request: Request, session: SessionDep, usuario: ApproveDep) -> dict:
    comissao = (
        await session.execute(
            select(ComissaoFinanceira).where(
                ComissaoFinanceira.id == comissao_id, ComissaoFinanceira.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if comissao is None:
        raise HTTPException(404, "Comissão não encontrada")
    if comissao.status != "pendente":
        raise HTTPException(409, f"Comissão já está \"{comissao.status}\", não pode ser paga novamente")
    comissao.status = "paga"
    comissao.pago_em = date.today()
    comissao.pago_por = usuario.ator
    _auditar(
        session,
        request,
        usuario,
        "pagar_comissao",
        f"comissao-financeira:{comissao.id}",
        {"valor": str(comissao.valor_comissao), "usuario_id": comissao.usuario_id},
    )
    await session.commit()
    return {"status": "paga"}

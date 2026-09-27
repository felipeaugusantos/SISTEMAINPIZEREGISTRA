"""Emissão de NFS-e -- achado FASE7-6 da auditoria (04/09/2026): não existia
nenhuma emissão de nota fiscal, só ReciboFinanceiro (recibo interno, sem
valor fiscal). Sempre uma ação explícita do operador para um lançamento de
receita específico -- nunca automático."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
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
                select(NotaFiscalServico, LancamentoFinanceiro, EmpresaCRM)
                .join(LancamentoFinanceiro, LancamentoFinanceiro.id == NotaFiscalServico.lancamento_id)
                .outerjoin(EmpresaCRM, EmpresaCRM.id == LancamentoFinanceiro.empresa_id)
                .where(NotaFiscalServico.organizacao_id == usuario.organizacao_id)
                .order_by(NotaFiscalServico.emitida_em.desc())
            )
        )
        .all()
    )
    return {
        "itens": [
            {
                "id": n.id,
                "lancamento_id": n.lancamento_id,
                "descricao_lancamento": lancamento.descricao,
                "cliente": empresa.nome if empresa else None,
                "numero": n.numero,
                "codigo_verificacao": n.codigo_verificacao,
                "valor": str(n.valor),
                "status": n.status,
                "emitida_em": n.emitida_em,
            }
            for n, lancamento, empresa in itens
        ]
    }


@router.get("/lancamentos-disponiveis")
async def listar_lancamentos_disponiveis_nfse(
    session: SessionDep, usuario: ViewDep, empresa_id: int = Query(ge=1)
) -> dict:
    """Lista títulos a receber do cliente escolhido, sem expor o ID como
    única forma de pesquisa nem permitir NFS-e duplicada para nota emitida."""
    empresa = (
        await session.execute(
            select(EmpresaCRM.id).where(
                EmpresaCRM.id == empresa_id,
                EmpresaCRM.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if empresa is None:
        raise HTTPException(404, "Cliente não encontrado")
    itens = (
        await session.execute(
            select(
                LancamentoFinanceiro.id,
                LancamentoFinanceiro.descricao,
                LancamentoFinanceiro.valor_total,
                LancamentoFinanceiro.competencia,
            )
            .where(
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.empresa_id == empresa_id,
                LancamentoFinanceiro.tipo == "receber",
                LancamentoFinanceiro.status != "cancelado",
                ~exists(
                    select(NotaFiscalServico.id).where(
                        NotaFiscalServico.lancamento_id == LancamentoFinanceiro.id,
                        NotaFiscalServico.status == "emitida",
                    )
                ),
            )
            .order_by(LancamentoFinanceiro.competencia.desc(), LancamentoFinanceiro.id.desc())
            .limit(100)
        )
    ).all()
    return {
        "itens": [
            {
                "id": item[0],
                "descricao": item[1],
                "valor": str(item[2]),
                "competencia": item[3],
            }
            for item in itens
        ]
    }


@router.post("/emitir", status_code=201)
async def emitir_nfse(dados: EmitirNfseInput, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    # Achado critico da auditoria financeira (15/09/2026): sem lock nem checagem
    # pre-existente, duplo clique ou retry apos timeout emitia duas NFS-e reais
    # para o mesmo lancamento. with_for_update() serializa tentativas concorrentes
    # -- a segunda so continua apos a primeira commitar, e entao enxerga a nota
    # "emitida" abaixo e recebe 409 sem chamar o adaptador de novo. Mesmo padrao
    # de lock ja usado em _parcela()/editar_lancamento()/cancelar() (financeiro.py).
    lancamento = (
        await session.execute(
            select(LancamentoFinanceiro)
            .where(
                LancamentoFinanceiro.id == dados.lancamento_id,
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if lancamento is None:
        raise HTTPException(404, "Lançamento não encontrado")
    if lancamento.tipo != "receber":
        raise HTTPException(422, "Só lançamentos de receita (tipo=receber) podem gerar NFS-e")
    if lancamento.status == "cancelado":
        raise HTTPException(422, "Lançamento cancelado não pode gerar NFS-e")

    nota_existente = (
        await session.execute(
            select(NotaFiscalServico.id).where(
                NotaFiscalServico.lancamento_id == lancamento.id,
                NotaFiscalServico.status == "emitida",
            )
        )
    ).scalar_one_or_none()
    if nota_existente is not None:
        raise HTTPException(409, "Já existe uma NFS-e emitida para este lançamento.")

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
        raise HTTPException(404, "Adaptador de NFS-e solicitado indisponível.") from exc

    try:
        emitida = await adaptador_obj.emitir(
            valor=Decimal(lancamento.valor_total),
            descricao_servico=lancamento.descricao,
            tomador_documento=empresa.documento,
            tomador_nome=empresa.nome,
            competencia=lancamento.competencia,
        )
    except Exception:  # noqa: BLE001 - provedor externo não é confiável para conteúdo de erro
        nota_erro = NotaFiscalServico(
            organizacao_id=usuario.organizacao_id,
            lancamento_id=lancamento.id,
            adaptador=dados.adaptador,
            valor=lancamento.valor_total,
            status="erro",
            # Não persistir texto arbitrário do provedor: pode conter dados do
            # tomador, payloads, tokens ou detalhes internos da integração.
            erro_detalhe="Falha de comunicação com o provedor de NFS-e.",
            emitida_por=usuario.ator,
        )
        session.add(nota_erro)
        await session.commit()
        raise HTTPException(502, "Não foi possível emitir a NFS-e no momento. Tente novamente mais tarde.") from None

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
    try:
        await session.flush()
    except IntegrityError:
        # Rede de seguranca do indice unico parcial (migration 562c64375107) --
        # so deveria disparar se, por algum motivo, o lock acima nao serializou
        # (ex.: backend sem suporte a FOR UPDATE em teste). Nunca some sem
        # explicacao: devolve 409 em vez de deixar o erro de banco vazar.
        await session.rollback()
        raise HTTPException(409, "Já existe uma NFS-e emitida para este lançamento.") from None
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

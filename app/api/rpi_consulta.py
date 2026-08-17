from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import Movimentacao, Processo, RpiImportacao

router = APIRouter(prefix="/v1/admin/rpi/consulta", tags=["consulta RPI"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("rpi.view"))]

SITUACOES_INPI: dict[str, tuple[str, ...]] = {
    "em_tramitacao": ("publicada", "em_exame", "oposicao"),
    "exigencia": ("exigencia",),
    "sobrestado": ("suspensa",),
    "recurso": ("recurso", "recurso_decidido"),
    "deferido": ("deferida", "deferida_parcial"),
    "registrado": ("registrada",),
    "indeferido": ("indeferida",),
    "encerrado": ("arquivada", "inexistente", "extinta", "cancelada"),
    "revisar": ("nao_classificada", "peticao_decidida"),
}


@router.get("/revistas")
async def listar_revistas_rpi(
    session: SessionDep,
    _usuario: ViewDep,
    limite: Annotated[int, Query(ge=1, le=10)] = 10,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    total = int(
        (await session.execute(
            select(func.count()).select_from(RpiImportacao).where(RpiImportacao.tipo == "marca")
        )).scalar_one()
    )
    itens = (
        await session.execute(
            select(RpiImportacao)
            .where(RpiImportacao.tipo == "marca")
            .order_by(RpiImportacao.numero_rpi.desc())
            .limit(limite)
            .offset(deslocamento)
        )
    ).scalars().all()
    return {
        "total": total,
        "limite": limite,
        "deslocamento": deslocamento,
        "itens": [
            {
                "numero_rpi": item.numero_rpi,
                "importado_em": item.importado_em,
                "registros_processados": item.registros_processados,
                "movimentacoes_processadas": item.movimentacoes_processadas,
                "status_integridade": item.status_integridade,
                "anomalias": item.anomalias or [],
            }
            for item in itens
        ],
    }


@router.get("")
async def consultar_rpi(
    session: SessionDep,
    _usuario: ViewDep,
    busca: Annotated[str | None, Query(max_length=150)] = None,
    numero_rpi: Annotated[int | None, Query(ge=1)] = None,
    situacao_inpi: Annotated[str | None, Query(max_length=30)] = None,
    inicio: date | None = None,
    fim: date | None = None,
    limite: Annotated[int, Query(ge=1, le=100)] = 20,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filtros = []
    if busca and busca.strip():
        termo = f"%{busca.strip()}%"
        filtros.append(
            or_(
                Processo.numero.ilike(termo),
                Processo.titulo.ilike(termo),
                Processo.procurador.ilike(termo),
                Movimentacao.descricao.ilike(termo),
                Movimentacao.codigo_despacho.ilike(termo),
            )
        )
    if numero_rpi is not None:
        filtros.append(Movimentacao.numero_rpi == numero_rpi)
    if situacao_inpi:
        codigos = SITUACOES_INPI.get(situacao_inpi.strip())
        if codigos is None:
            raise HTTPException(status_code=422, detail="Situação do INPI inválida.")
        filtros.append(Processo.situacao_normalizada.in_(codigos))
    if inicio is not None:
        filtros.append(Movimentacao.data_rpi >= inicio)
    if fim is not None:
        filtros.append(Movimentacao.data_rpi <= fim)

    count_query = (
        select(func.count(Movimentacao.id))
        .select_from(Movimentacao)
        .join(Processo, Processo.id == Movimentacao.processo_id)
        .where(*filtros)
    )
    total = int((await session.execute(count_query)).scalar_one())
    tem_mais = False
    linhas = (
        await session.execute(
            select(Movimentacao, Processo)
            .join(Processo, Processo.id == Movimentacao.processo_id)
            .where(*filtros)
            .order_by(Movimentacao.data_rpi.desc(), Movimentacao.numero_rpi.desc(), Movimentacao.id.desc())
            .limit(limite + 1)
            .offset(deslocamento)
        )
    ).all()
    tem_mais = len(linhas) > limite
    linhas = linhas[:limite]
    return {
        "total": total,
        "limite": limite,
        "deslocamento": deslocamento,
        "tem_mais": tem_mais,
        "itens": [
            {
                "id": movimentacao.id,
                "numero_rpi": movimentacao.numero_rpi,
                "data_rpi": movimentacao.data_rpi,
                "codigo_despacho": movimentacao.codigo_despacho,
                "descricao": movimentacao.descricao,
                "processo_id": processo.id,
                "numero": processo.numero,
                "titulo": processo.titulo,
                "procurador": processo.procurador,
                "situacao": processo.situacao,
            }
            for movimentacao, processo in linhas
        ],
    }

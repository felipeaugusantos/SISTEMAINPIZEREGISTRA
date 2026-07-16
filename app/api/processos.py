from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models import Processo, TipoProcesso, Titular, processo_titulares
from app.normalization import normalizar_numero_processo
from app.schemas import ProcessoResponse, ResultadoBusca

router = APIRouter(prefix="/v1/processos", tags=["processos"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
NomeBusca = Annotated[
    str,
    Query(
        min_length=2,
        max_length=100,
        description="Nome da marca, título da patente ou nome do titular",
    ),
]
LimiteBusca = Annotated[int, Query(ge=1, le=50)]
DeslocamentoBusca = Annotated[int, Query(ge=0)]


@router.get("", response_model=ResultadoBusca)
async def pesquisar_processos(
    nome: NomeBusca,
    session: SessionDep,
    tipo: TipoProcesso | None = None,
    limite: LimiteBusca = 20,
    deslocamento: DeslocamentoBusca = 0,
) -> ResultadoBusca:
    termo = f"%{nome.strip()}%"
    termo_sem_acentos = func.immutable_unaccent(termo)
    filtro_tipo = Processo.tipo == tipo if tipo is not None else True

    ids_por_titulo = select(Processo.id.label("processo_id")).where(
        func.immutable_unaccent(Processo.titulo).ilike(termo_sem_acentos),
        filtro_tipo,
    )
    ids_por_titular = (
        select(processo_titulares.c.processo_id.label("processo_id"))
        .join(Titular, Titular.id == processo_titulares.c.titular_id)
        .join(Processo, Processo.id == processo_titulares.c.processo_id)
        .where(
            func.immutable_unaccent(Titular.nome).ilike(termo_sem_acentos),
            filtro_tipo,
        )
    )
    ids_encontrados = union(ids_por_titulo, ids_por_titular).subquery()

    consulta_total = select(func.count()).select_from(ids_encontrados)
    total = (await session.execute(consulta_total)).scalar_one()

    consulta = (
        select(Processo)
        .join(ids_encontrados, ids_encontrados.c.processo_id == Processo.id)
        .options(selectinload(Processo.titulares))
        .order_by(Processo.atualizado_em.desc(), Processo.numero)
        .limit(limite)
        .offset(deslocamento)
    )
    processos = (await session.execute(consulta)).scalars().all()

    return ResultadoBusca(
        total=total,
        limite=limite,
        deslocamento=deslocamento,
        itens=processos,
    )


@router.get("/{numero}", response_model=ProcessoResponse)
async def buscar_processo(numero: str, session: SessionDep) -> Processo:
    consulta = (
        select(Processo)
        .where(Processo.numero_normalizado == normalizar_numero_processo(numero))
        .options(
            selectinload(Processo.titulares),
            selectinload(Processo.movimentacoes),
            selectinload(Processo.classificacoes),
        )
    )
    processo = (await session.execute(consulta)).scalar_one_or_none()

    if processo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Processo não encontrado",
        )

    return processo

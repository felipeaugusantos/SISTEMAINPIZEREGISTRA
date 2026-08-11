import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.crm import obter_ou_criar_empresa
from app.database import get_session
from app.models import (
    EmpresaCRM,
    EventoAuditoria,
    Movimentacao,
    Processo,
    ProcessoMonitorado,
    RpiImportacao,
    TipoProcesso,
    UsuarioOperacoes,
    processo_titulares,
)
from app.normalization import normalizar_numero_processo
from app.proxy import cliente_ip

router = APIRouter(prefix="/v1/admin/carteira", tags=["processos monitorados"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("portfolio.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("portfolio.manage"))]


def _normalizar_busca(valor: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valor.strip())
        if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.casefold().split())


def _expressao_procurador():
    return func.regexp_replace(
        func.trim(func.immutable_unaccent(func.lower(Processo.procurador))),
        r"\s+",
        " ",
        "g",
    )


class VinculoBase(BaseModel):
    empresa_id: int | None = Field(default=None, ge=1)
    empresa_nome: str | None = Field(default=None, min_length=2, max_length=200)
    responsavel_id: int | None = Field(default=None, ge=1)
    observacoes: str | None = Field(default=None, max_length=4000)

    @field_validator("empresa_nome", "observacoes", mode="before")
    @classmethod
    def limpar_opcionais(cls, valor: str | None) -> str | None:
        return valor.strip() or None if isinstance(valor, str) else valor


class CadastroManual(VinculoBase):
    numero: str = Field(min_length=5, max_length=50)


class VinculoLote(VinculoBase):
    processo_ids: list[int] = Field(min_length=1, max_length=200)


class VinculoProcurador(VinculoBase):
    procurador: str = Field(min_length=2, max_length=300)
    modo: Literal["exato", "contem", "variacoes"] = "exato"
    maximo: int = Field(default=5000, ge=1, le=5000)


class AtualizacaoMonitoramento(BaseModel):
    status: Literal["ativo", "pausado", "encerrado", "arquivado"] | None = None
    empresa_id: int | None = Field(default=None, ge=1)
    empresa_nome: str | None = Field(default=None, min_length=2, max_length=200)
    responsavel_id: int | None = Field(default=None, ge=1)
    remover_responsavel: bool = False
    observacoes: str | None = Field(default=None, max_length=4000)


async def _empresa(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    empresa_id: int | None,
    empresa_nome: str | None,
) -> EmpresaCRM | None:
    if empresa_id is not None:
        empresa = (
            await session.execute(
                select(EmpresaCRM).where(
                    EmpresaCRM.id == empresa_id,
                    EmpresaCRM.organizacao_id == usuario.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if empresa is None:
            raise HTTPException(404, "Empresa não encontrada")
        return empresa
    return await obter_ou_criar_empresa(session, usuario.organizacao_id, empresa_nome)


async def _validar_responsavel(
    session: AsyncSession, usuario: UsuarioAutenticado, responsavel_id: int | None
) -> None:
    if responsavel_id is None:
        return
    existe = await session.scalar(
        select(UsuarioOperacoes.id).where(
            UsuarioOperacoes.id == responsavel_id,
            UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
            UsuarioOperacoes.ativo.is_(True),
        )
    )
    if existe is None:
        raise HTTPException(404, "Responsável não encontrado")


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


async def _vincular_ids(
    session: AsyncSession,
    request: Request,
    usuario: UsuarioAutenticado,
    processo_ids: list[int],
    dados: VinculoBase,
    *,
    origem: str,
    procurador_origem: str | None = None,
) -> dict:
    ids = list(dict.fromkeys(processo_ids))
    processos_existentes = set(
        (
            await session.execute(
                select(Processo.id).where(
                    Processo.id.in_(ids), Processo.tipo == TipoProcesso.MARCA
                )
            )
        ).scalars().all()
    )
    ja_vinculados = set(
        (
            await session.execute(
                select(ProcessoMonitorado.processo_id).where(
                    ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
                    ProcessoMonitorado.processo_id.in_(processos_existentes),
                )
            )
        ).scalars().all()
    )
    empresa = await _empresa(
        session, usuario, dados.empresa_id, dados.empresa_nome
    )
    await _validar_responsavel(session, usuario, dados.responsavel_id)
    novos = processos_existentes - ja_vinculados
    for processo_id in novos:
        session.add(
            ProcessoMonitorado(
                organizacao_id=usuario.organizacao_id,
                processo_id=processo_id,
                empresa_id=empresa.id if empresa else None,
                responsavel_id=dados.responsavel_id,
                status="ativo",
                origem=origem,
                procurador_origem=procurador_origem,
                observacoes=dados.observacoes,
                vinculado_por=usuario.ator,
            )
        )
    resultado = {
        "encontrados": len(ids),
        "vinculados": len(novos),
        "ja_vinculados": len(ja_vinculados),
        "nao_encontrados": len(set(ids) - processos_existentes),
        "empresa": empresa.nome if empresa else None,
    }
    _auditar(
        session,
        request,
        usuario,
        "vincular_processos",
        f"carteira:{origem}",
        {**resultado, "procurador": procurador_origem},
    )
    await session.commit()
    return resultado


@router.get("")
async def listar_carteira(
    session: SessionDep,
    usuario: ViewDep,
    busca: Annotated[str | None, Query(max_length=150)] = None,
    status: Annotated[str | None, Query(max_length=20)] = None,
    limite: Annotated[int, Query(ge=1, le=100)] = 50,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    resumo_linhas = (
        await session.execute(
            select(ProcessoMonitorado.status, func.count())
            .where(ProcessoMonitorado.organizacao_id == usuario.organizacao_id)
            .group_by(ProcessoMonitorado.status)
        )
    ).all()
    resumo = {chave: int(total_status) for chave, total_status in resumo_linhas}
    filtros = [ProcessoMonitorado.organizacao_id == usuario.organizacao_id]
    if status:
        filtros.append(ProcessoMonitorado.status == status)
    if busca:
        termo = f"%{_normalizar_busca(busca)}%"
        filtros.append(
            or_(
                Processo.numero_normalizado.ilike(f"%{normalizar_numero_processo(busca)}%"),
                func.immutable_unaccent(func.lower(Processo.titulo)).ilike(termo),
                _expressao_procurador().ilike(termo),
                func.immutable_unaccent(func.lower(EmpresaCRM.nome)).ilike(termo),
            )
        )
    base = (
        select(ProcessoMonitorado)
        .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
        .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
        .where(*filtros)
    )
    total = int(
        (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    )
    ultima_rpi = (
        select(Movimentacao.numero_rpi)
        .where(Movimentacao.processo_id == Processo.id)
        .order_by(Movimentacao.data_rpi.desc(), Movimentacao.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    ultima_data = (
        select(Movimentacao.data_rpi)
        .where(Movimentacao.processo_id == Processo.id)
        .order_by(Movimentacao.data_rpi.desc(), Movimentacao.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    ultima_descricao = (
        select(Movimentacao.descricao)
        .where(Movimentacao.processo_id == Processo.id)
        .order_by(Movimentacao.data_rpi.desc(), Movimentacao.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    linhas = (
        await session.execute(
            select(
                ProcessoMonitorado,
                Processo,
                EmpresaCRM.nome,
                UsuarioOperacoes.nome,
                ultima_rpi,
                ultima_data,
                ultima_descricao,
            )
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
            .outerjoin(
                UsuarioOperacoes, UsuarioOperacoes.id == ProcessoMonitorado.responsavel_id
            )
            .where(*filtros)
            .order_by(ProcessoMonitorado.atualizado_em.desc(), Processo.numero)
            .limit(limite)
            .offset(deslocamento)
        )
    ).all()
    return {
        "total": total,
        "limite": limite,
        "deslocamento": deslocamento,
        "resumo": {
            "total": sum(resumo.values()),
            "ativos": resumo.get("ativo", 0),
            "pausados": resumo.get("pausado", 0),
            "encerrados": resumo.get("encerrado", 0),
            "arquivados": resumo.get("arquivado", 0),
        },
        "itens": [
            {
                "id": monitorado.id,
                "numero": processo.numero,
                "titulo": processo.titulo,
                "situacao": processo.situacao,
                "data_deposito": processo.data_deposito,
                "procurador": processo.procurador,
                "fonte": processo.fonte,
                "processo_atualizado_em": processo.atualizado_em,
                "status": monitorado.status,
                "origem": monitorado.origem,
                "empresa_id": monitorado.empresa_id,
                "empresa": empresa_nome,
                "responsavel_id": monitorado.responsavel_id,
                "responsavel": responsavel_nome,
                "observacoes": monitorado.observacoes,
                "criado_em": monitorado.criado_em,
                "ultima_movimentacao": (
                    {
                        "numero_rpi": numero_rpi,
                        "data": data_rpi,
                        "descricao": descricao,
                    }
                    if numero_rpi is not None
                    else None
                ),
            }
            for (
                monitorado,
                processo,
                empresa_nome,
                responsavel_nome,
                numero_rpi,
                data_rpi,
                descricao,
            ) in linhas
        ],
    }


@router.get("/empresas")
async def listar_empresas(
    session: SessionDep,
    usuario: ViewDep,
    busca: Annotated[str | None, Query(max_length=100)] = None,
) -> list[dict]:
    consulta = select(EmpresaCRM).where(
        EmpresaCRM.organizacao_id == usuario.organizacao_id
    )
    if busca:
        consulta = consulta.where(
            func.immutable_unaccent(func.lower(EmpresaCRM.nome)).ilike(
                f"%{_normalizar_busca(busca)}%"
            )
        )
    empresas = (
        await session.execute(consulta.order_by(EmpresaCRM.nome).limit(100))
    ).scalars().all()
    return [{"id": empresa.id, "nome": empresa.nome} for empresa in empresas]


@router.get("/responsaveis")
async def listar_responsaveis(
    session: SessionDep, usuario: ViewDep
) -> list[dict]:
    pessoas = (
        await session.execute(
            select(UsuarioOperacoes)
            .where(
                UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
                UsuarioOperacoes.ativo.is_(True),
            )
            .order_by(UsuarioOperacoes.nome)
        )
    ).scalars().all()
    return [{"id": pessoa.id, "nome": pessoa.nome} for pessoa in pessoas]


@router.get("/procuradores")
async def sugerir_procuradores(
    session: SessionDep,
    _usuario: ViewDep,
    busca: Annotated[str, Query(min_length=2, max_length=120)],
) -> list[str]:
    termo = f"%{_normalizar_busca(busca)}%"
    nomes = (
        await session.execute(
            select(Processo.procurador)
            .where(
                Processo.tipo == TipoProcesso.MARCA,
                Processo.procurador.is_not(None),
                _expressao_procurador().ilike(termo),
            )
            .distinct()
            .order_by(Processo.procurador)
            .limit(20)
        )
    ).scalars().all()
    return list(nomes)


def _filtro_procurador(procurador: str, modo: str):
    normalizado = _normalizar_busca(procurador)
    expressao = _expressao_procurador()
    if modo == "exato":
        return expressao == normalizado
    if modo == "variacoes":
        return and_(*(expressao.ilike(f"%{termo}%") for termo in normalizado.split()))
    return expressao.ilike(f"%{normalizado}%")


@router.get("/buscar-procurador")
async def buscar_por_procurador(
    session: SessionDep,
    usuario: ViewDep,
    procurador: Annotated[str, Query(min_length=2, max_length=300)],
    modo: Literal["exato", "contem", "variacoes"] = "exato",
    limite: Annotated[int, Query(ge=1, le=100)] = 50,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filtro = _filtro_procurador(procurador, modo)
    filtros = [Processo.tipo == TipoProcesso.MARCA, filtro]
    total, titulares = (
        await session.execute(
            select(
                func.count(func.distinct(Processo.id)),
                func.count(func.distinct(processo_titulares.c.titular_id)),
            )
            .select_from(Processo)
            .outerjoin(
                processo_titulares,
                processo_titulares.c.processo_id == Processo.id,
            )
            .where(*filtros)
        )
    ).one()
    primeira_rpi, ultima_rpi, rpis_importadas = (
        await session.execute(
            select(
                func.min(RpiImportacao.numero_rpi),
                func.max(RpiImportacao.numero_rpi),
                func.count(),
            ).where(RpiImportacao.tipo == "marca")
        )
    ).one()
    vinculo = (
        select(ProcessoMonitorado.id)
        .where(
            ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
            ProcessoMonitorado.processo_id == Processo.id,
        )
        .limit(1)
        .scalar_subquery()
    )
    linhas = (
        await session.execute(
            select(Processo, vinculo)
            .where(*filtros)
            .order_by(Processo.atualizado_em.desc(), Processo.numero)
            .limit(limite)
            .offset(deslocamento)
        )
    ).all()
    variacoes: dict[str, int] = {}
    for processo, _ in linhas:
        if processo.procurador:
            variacoes[processo.procurador] = variacoes.get(processo.procurador, 0) + 1
    return {
        "total": int(total or 0),
        "total_processos": int(total or 0),
        "total_titulares": int(titulares or 0),
        "limite": limite,
        "deslocamento": deslocamento,
        "modo": modo,
        "variacoes": [
            {"nome": nome, "processos_na_pagina": quantidade}
            for nome, quantidade in sorted(
                variacoes.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        ],
        "cobertura": {
            "primeira_rpi": primeira_rpi,
            "ultima_rpi": ultima_rpi,
            "rpis_importadas": int(rpis_importadas or 0),
            "aviso": (
                "O resultado depende do nome do procurador publicado nas RPIs e pode não "
                "representar toda a carteira histórica existente no INPI."
            ),
        },
        "itens": [
            {
                "processo_id": processo.id,
                "numero": processo.numero,
                "titulo": processo.titulo,
                "data_deposito": processo.data_deposito,
                "situacao": processo.situacao,
                "procurador": processo.procurador,
                "fonte": processo.fonte,
                "monitorado_id": monitorado_id,
            }
            for processo, monitorado_id in linhas
        ],
    }


@router.post("/manual", status_code=201)
async def cadastrar_manual(
    dados: CadastroManual,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    processo = (
        await session.execute(
            select(Processo).where(
                Processo.numero_normalizado == normalizar_numero_processo(dados.numero),
                Processo.tipo == TipoProcesso.MARCA,
            )
        )
    ).scalar_one_or_none()
    if processo is None:
        raise HTTPException(
            404,
            "Processo não localizado na base RPI. Sincronize as revistas antes de cadastrar.",
        )
    resultado = await _vincular_ids(
        session, request, usuario, [processo.id], dados, origem="manual"
    )
    return {**resultado, "numero": processo.numero}


@router.post("/vincular-lote", status_code=201)
async def vincular_lote(
    dados: VinculoLote,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    return await _vincular_ids(
        session, request, usuario, dados.processo_ids, dados, origem="selecao_procurador"
    )


@router.post("/vincular-procurador", status_code=201)
async def vincular_todos_do_procurador(
    dados: VinculoProcurador,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    ids = list(
        (
            await session.execute(
                select(Processo.id)
                .where(
                    Processo.tipo == TipoProcesso.MARCA,
                    _filtro_procurador(dados.procurador, dados.modo),
                )
                .order_by(Processo.id)
                .limit(dados.maximo)
            )
        ).scalars().all()
    )
    if not ids:
        raise HTTPException(404, "Nenhum processo encontrado para o procurador informado")
    return await _vincular_ids(
        session,
        request,
        usuario,
        ids,
        dados,
        origem="procurador",
        procurador_origem=dados.procurador,
    )


@router.patch("/{monitorado_id}")
async def atualizar_monitoramento(
    monitorado_id: int,
    dados: AtualizacaoMonitoramento,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    monitorado = (
        await session.execute(
            select(ProcessoMonitorado).where(
                ProcessoMonitorado.id == monitorado_id,
                ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if monitorado is None:
        raise HTTPException(404, "Processo monitorado não encontrado")
    antes = {
        "status": monitorado.status,
        "empresa_id": monitorado.empresa_id,
        "responsavel_id": monitorado.responsavel_id,
    }
    if dados.status is not None:
        monitorado.status = dados.status
    if dados.empresa_id is not None or dados.empresa_nome:
        empresa = await _empresa(
            session, usuario, dados.empresa_id, dados.empresa_nome
        )
        monitorado.empresa_id = empresa.id if empresa else None
    if dados.remover_responsavel:
        monitorado.responsavel_id = None
    elif dados.responsavel_id is not None:
        await _validar_responsavel(session, usuario, dados.responsavel_id)
        monitorado.responsavel_id = dados.responsavel_id
    if dados.observacoes is not None:
        monitorado.observacoes = dados.observacoes.strip() or None
    monitorado.atualizado_em = datetime.now(UTC)
    _auditar(
        session,
        request,
        usuario,
        "atualizar_carteira",
        f"processo-monitorado:{monitorado.id}",
        {
            "antes": antes,
            "depois": {
                "status": monitorado.status,
                "empresa_id": monitorado.empresa_id,
                "responsavel_id": monitorado.responsavel_id,
            },
        },
    )
    await session.commit()
    return {"status": "ok", "id": monitorado.id}

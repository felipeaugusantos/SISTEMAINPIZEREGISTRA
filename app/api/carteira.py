import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.cli.consolidar_situacoes_marcas import consolidar_situacao
from app.crm import obter_ou_criar_empresa, verificar_conflito_interesse
from app.database import get_session
from app.importacao_planilha import TAMANHO_MAXIMO_IMPORTACAO, ler_planilha, valor_coluna
from app.models import (
    EmpresaCRM,
    EventoAuditoria,
    HistoricoEtapaCarteira,
    Lead,
    Movimentacao,
    PreCadastroProcesso,
    Processo,
    ProcessoMonitorado,
    RpiImportacao,
    TipoProcesso,
    Titular,
    UsuarioOperacoes,
    processo_titulares,
)
from app.normalization import normalizar_numero_processo
from app.proxy import cliente_ip
from app.relatorios import gerar_pdf_processo_monitorado

router = APIRouter(prefix="/v1/admin/carteira", tags=["processos monitorados"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("portfolio.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("portfolio.manage"))]

ETAPAS_KANBAN: tuple[tuple[str, str], ...] = (
    ("triagem", "Novo / Triagem"),
    ("aguardando_documentos", "Aguardando documentos"),
    ("documentacao_gru", "Documentação e GRU"),
    ("protocolado", "Protocolado"),
    ("aguardando_inpi", "Aguardando INPI"),
    ("exigencia_recurso", "Exigência / Recurso"),
    ("deferido_concessao", "Deferido / Concessão"),
    ("encerrado", "Encerrado"),
)
GRUPOS_SITUACAO_INPI: tuple[tuple[str, str], ...] = (
    ("em_tramitacao", "Em tramitação"),
    ("exigencia", "Exigência"),
    ("sobrestado", "Sobrestado"),
    ("recurso", "Recurso / 2ª instância"),
    ("deferido", "Deferido"),
    ("registrado", "Registro concedido"),
    ("indeferido", "Indeferido"),
    ("encerrado", "Arquivado / Extinto"),
    ("revisar", "Revisar classificação"),
)
EtapaKanban = Literal[
    "triagem",
    "aguardando_documentos",
    "documentacao_gru",
    "protocolado",
    "aguardando_inpi",
    "exigencia_recurso",
    "deferido_concessao",
    "encerrado",
]
# Mesmos rótulos de KANBAN_STAGES em app/web/static/admin-carteira.js --
# usados no relatório de acompanhamento em PDF (ver gerar_relatorio_pdf).
ETAPA_KANBAN_LABELS: dict[str, str] = {
    "triagem": "Novo / Triagem",
    "aguardando_documentos": "Aguardando documentos",
    "documentacao_gru": "Documentação e GRU",
    "protocolado": "Protocolado",
    "aguardando_inpi": "Aguardando INPI",
    "exigencia_recurso": "Exigência / Recurso",
    "deferido_concessao": "Deferido / Concessão",
    "encerrado": "Encerrado",
}


def _normalizar_busca(valor: str) -> str:
    sem_acentos = "".join(
        caractere for caractere in unicodedata.normalize("NFKD", valor.strip()) if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.casefold().split())


def _expressao_procurador():
    return func.regexp_replace(
        func.trim(func.immutable_unaccent(func.lower(Processo.procurador))),
        r"\s+",
        " ",
        "g",
    )


def _expressao_grupo_situacao_inpi():
    codigo = Processo.situacao_normalizada
    return case(
        (codigo.in_(("publicada", "em_exame", "oposicao")), "em_tramitacao"),
        (codigo == "exigencia", "exigencia"),
        (codigo == "suspensa", "sobrestado"),
        (codigo.in_(("recurso", "recurso_decidido")), "recurso"),
        (codigo.in_(("deferida", "deferida_parcial")), "deferido"),
        (codigo == "registrada", "registrado"),
        (codigo == "indeferida", "indeferido"),
        (
            codigo.in_(("arquivada", "inexistente", "extinta", "cancelada")),
            "encerrado",
        ),
        else_="revisar",
    )


def _nome_grupo_situacao_inpi(chave: str) -> str:
    return dict(GRUPOS_SITUACAO_INPI).get(chave, "Revisar classificação")


def _validar_grupo_situacao_inpi(valor: str | None) -> str | None:
    valor = (valor or "").strip()
    if not valor:
        return None
    if valor not in dict(GRUPOS_SITUACAO_INPI):
        raise HTTPException(status_code=422, detail="Situação do INPI inválida.")
    return valor


def _grupo_situacao_valor(codigo: str | None) -> str:
    if codigo in {"publicada", "em_exame", "oposicao"}:
        return "em_tramitacao"
    if codigo == "exigencia":
        return "exigencia"
    if codigo == "suspensa":
        return "sobrestado"
    if codigo in {"recurso", "recurso_decidido"}:
        return "recurso"
    if codigo in {"deferida", "deferida_parcial"}:
        return "deferido"
    if codigo == "registrada":
        return "registrado"
    if codigo == "indeferida":
        return "indeferido"
    if codigo in {"arquivada", "inexistente", "extinta", "cancelada"}:
        return "encerrado"
    return "revisar"


def _titulo_exibicao(processo: Processo) -> str:
    titulo = (processo.titulo or "").strip()
    if titulo:
        return titulo
    if _normalizar_busca(processo.apresentacao or "") == "figurativa":
        return "Marca figurativa (sem elemento nominativo)"
    if processo.situacao_normalizada == "inexistente":
        return "Pedido inexistente — título não publicado"
    return "Título não informado pelo INPI"


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
    titular: str = Field(min_length=2, max_length=300)

    @field_validator("titular", mode="before")
    @classmethod
    def _limpar_titular(cls, valor: object) -> str | None:
        return valor.strip() if isinstance(valor, str) else valor


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
    lead_id: int | None = Field(default=None, ge=1)
    remover_lead: bool = False
    observacoes: str | None = Field(default=None, max_length=4000)
    procurador: str | None = Field(default=None, max_length=500)
    etapa_kanban: EtapaKanban | None = None
    prioridade: Literal["alta", "media", "baixa"] | None = None

    @field_validator("procurador", mode="before")
    @classmethod
    def _limpar_procurador(cls, valor: object) -> str | None:
        if valor is None:
            return None
        limpo = re.sub(r"\s+", " ", str(valor)).strip()
        return limpo or None


class GeracaoRelatorioProcessoMonitorado(BaseModel):
    # Achado do usuário (08/09/2026): texto digitado na hora de gerar o PDF,
    # específico deste relatório -- não persiste no cadastro interno do
    # processo (esse já tem seu próprio campo `observacoes`, de uso interno).
    observacoes_relatorio: str | None = Field(default=None, max_length=4000)


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


async def _validar_lead(session: AsyncSession, usuario: UsuarioAutenticado, lead_id: int | None) -> None:
    if lead_id is None:
        return
    existe = await session.scalar(
        select(Lead.id).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id)
    )
    if existe is None:
        raise HTTPException(404, "Lead não encontrado")


async def _validar_responsavel(session: AsyncSession, usuario: UsuarioAutenticado, responsavel_id: int | None) -> None:
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


async def _leads_por_numero_processo(
    session: AsyncSession, organizacao_id: int, processo_ids: set[int]
) -> dict[int, int]:
    """Casa processo_id -> lead_id comparando Lead.processo_numero (texto livre,
    digitado no CRM) com Processo.numero_normalizado -- fecha o achado da
    auditoria em que essa ligação existia só como coincidência de string, nunca
    verificada. Só preenche; nunca sobrescreve um vínculo já feito manualmente
    (chamado apenas para processos ainda sem ProcessoMonitorado)."""
    if not processo_ids:
        return {}
    normalizado = func.upper(func.regexp_replace(Lead.processo_numero, "[^A-Za-z0-9]", "", "g"))
    linhas = (
        await session.execute(
            select(Processo.id, Lead.id)
            .select_from(Processo)
            .join(
                Lead,
                and_(
                    Lead.organizacao_id == organizacao_id,
                    Lead.arquivado_em.is_(None),
                    Lead.processo_numero.isnot(None),
                    normalizado == Processo.numero_normalizado,
                ),
            )
            .where(Processo.id.in_(processo_ids))
        )
    ).all()
    resultado: dict[int, int] = {}
    for processo_id, lead_id in linhas:
        resultado.setdefault(processo_id, lead_id)
    return resultado


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
        (await session.execute(select(Processo.id).where(Processo.id.in_(ids), Processo.tipo == TipoProcesso.MARCA)))
        .scalars()
        .all()
    )
    ja_vinculados = set(
        (
            await session.execute(
                select(ProcessoMonitorado.processo_id).where(
                    ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
                    ProcessoMonitorado.processo_id.in_(processos_existentes),
                )
            )
        )
        .scalars()
        .all()
    )
    empresa = await _empresa(session, usuario, dados.empresa_id, dados.empresa_nome)
    await _validar_responsavel(session, usuario, dados.responsavel_id)
    novos = processos_existentes - ja_vinculados
    leads_por_processo = await _leads_por_numero_processo(session, usuario.organizacao_id, novos)
    for processo_id in novos:
        session.add(
            ProcessoMonitorado(
                organizacao_id=usuario.organizacao_id,
                processo_id=processo_id,
                empresa_id=empresa.id if empresa else None,
                responsavel_id=dados.responsavel_id,
                lead_id=leads_por_processo.get(processo_id),
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
    etapa: Annotated[EtapaKanban | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=20)] = 20,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
    situacao_inpi: Annotated[str | None, Query(max_length=30)] = None,
) -> dict:
    situacao_inpi = _validar_grupo_situacao_inpi(situacao_inpi)
    resumo_linhas = (
        await session.execute(
            select(ProcessoMonitorado.status, func.count())
            .where(ProcessoMonitorado.organizacao_id == usuario.organizacao_id)
            .group_by(ProcessoMonitorado.status)
        )
    ).all()
    resumo = {chave: int(total_status) for chave, total_status in resumo_linhas}
    grupo_resumo = _expressao_grupo_situacao_inpi()
    resumo_situacoes_linhas = (
        await session.execute(
            select(grupo_resumo.label("grupo"), func.count())
            .select_from(ProcessoMonitorado)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .where(ProcessoMonitorado.organizacao_id == usuario.organizacao_id)
            .group_by(grupo_resumo)
        )
    ).all()
    resumo_situacoes = {chave: int(total_situacao) for chave, total_situacao in resumo_situacoes_linhas}
    filtros = [ProcessoMonitorado.organizacao_id == usuario.organizacao_id]
    if status:
        filtros.append(ProcessoMonitorado.status == status)
    if etapa:
        filtros.append(ProcessoMonitorado.etapa_kanban == etapa)
    if situacao_inpi:
        filtros.append(_expressao_grupo_situacao_inpi() == situacao_inpi)
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
    prioridade_situacao = case(
        (_expressao_grupo_situacao_inpi() == "deferido", 1),
        (_expressao_grupo_situacao_inpi() == "em_tramitacao", 2),
        (_expressao_grupo_situacao_inpi() == "registrado", 3),
        (_expressao_grupo_situacao_inpi() == "indeferido", 4),
        (_expressao_grupo_situacao_inpi() == "encerrado", 5),
        else_=6,
    )
    total = int((await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
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
            .outerjoin(UsuarioOperacoes, UsuarioOperacoes.id == ProcessoMonitorado.responsavel_id)
            .where(*filtros)
            .order_by(
                case(
                    (ProcessoMonitorado.prioridade == "alta", 0),
                    (ProcessoMonitorado.prioridade == "media", 1),
                    else_=2,
                ),
                prioridade_situacao,
                ProcessoMonitorado.atualizado_em.desc(),
                Processo.numero,
            )
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
            "pausados": resumo.get("pausado", 0),
            "deferidos": resumo_situacoes.get("deferido", 0),
            "arquivados_extintos": resumo_situacoes.get("encerrado", 0),
            "indeferidos": resumo_situacoes.get("indeferido", 0),
            "registros_concedidos": resumo_situacoes.get("registrado", 0),
            "em_tramitacao": resumo_situacoes.get("em_tramitacao", 0),
        },
        "itens": [
            {
                "id": monitorado.id,
                "numero": processo.numero,
                "titulo": processo.titulo,
                "titulo_exibicao": _titulo_exibicao(processo),
                "situacao": processo.situacao,
                "situacao_normalizada": processo.situacao_normalizada,
                "grupo_situacao_inpi": _grupo_situacao_valor(processo.situacao_normalizada),
                "grupo_situacao_inpi_nome": _nome_grupo_situacao_inpi(
                    _grupo_situacao_valor(processo.situacao_normalizada)
                ),
                "data_deposito": processo.data_deposito,
                "procurador": processo.procurador,
                "fonte": processo.fonte,
                "processo_atualizado_em": processo.atualizado_em,
                "status": monitorado.status,
                "prioridade": monitorado.prioridade,
                "etapa_kanban": monitorado.etapa_kanban,
                "etapa_atualizada_em": monitorado.etapa_atualizada_em,
                "etapa_atualizada_por": monitorado.etapa_atualizada_por,
                "origem": monitorado.origem,
                "empresa_id": monitorado.empresa_id,
                "empresa": empresa_nome,
                "responsavel_id": monitorado.responsavel_id,
                "responsavel": responsavel_nome,
                "lead_id": monitorado.lead_id,
                "lead_marca": monitorado.lead.marca if monitorado.lead else None,
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
        "tem_mais": deslocamento + len(linhas) < total,
    }


@router.get("/kanban")
async def listar_kanban(
    session: SessionDep,
    usuario: ViewDep,
    busca: Annotated[str | None, Query(max_length=150)] = None,
    status: Annotated[str | None, Query(max_length=20)] = None,
    situacao_inpi: Annotated[str | None, Query(max_length=30)] = None,
) -> dict:
    """Entrega no máximo 20 cartões por etapa e o total real de cada coluna."""
    situacao_inpi = _validar_grupo_situacao_inpi(situacao_inpi)
    filtros = [ProcessoMonitorado.organizacao_id == usuario.organizacao_id]
    if status:
        filtros.append(ProcessoMonitorado.status == status)
    if situacao_inpi:
        filtros.append(_expressao_grupo_situacao_inpi() == situacao_inpi)
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

    contagens = dict(
        (
            await session.execute(
                select(ProcessoMonitorado.etapa_kanban, func.count())
                .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
                .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
                .where(*filtros)
                .group_by(ProcessoMonitorado.etapa_kanban)
            )
        ).all()
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
    colunas = []
    for chave, titulo in ETAPAS_KANBAN:
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
                    UsuarioOperacoes,
                    UsuarioOperacoes.id == ProcessoMonitorado.responsavel_id,
                )
                .where(*filtros, ProcessoMonitorado.etapa_kanban == chave)
                .order_by(
                    ProcessoMonitorado.ordem_kanban,
                    ProcessoMonitorado.etapa_atualizada_em.desc(),
                    ProcessoMonitorado.id.desc(),
                )
                .limit(20)
            )
        ).all()
        itens = []
        for (
            monitorado,
            processo,
            empresa_nome,
            responsavel_nome,
            numero_rpi,
            data_rpi,
            descricao,
        ) in linhas:
            itens.append(
                {
                    "id": monitorado.id,
                    "numero": processo.numero,
                    "titulo": processo.titulo,
                    "titulo_exibicao": _titulo_exibicao(processo),
                    "situacao": processo.situacao,
                    "data_deposito": processo.data_deposito,
                    "status": monitorado.status,
                    "etapa_kanban": monitorado.etapa_kanban,
                    "empresa": empresa_nome,
                    "responsavel": responsavel_nome,
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
            )
        total = int(contagens.get(chave, 0))
        colunas.append(
            {
                "chave": chave,
                "titulo": titulo,
                "total": total,
                "limite": 20,
                "tem_mais": total > len(itens),
                "itens": itens,
            }
        )
    return {"total": sum(int(valor) for valor in contagens.values()), "colunas": colunas}


@router.get("/kanban-inpi")
async def listar_kanban_inpi(
    session: SessionDep,
    usuario: ViewDep,
    busca: Annotated[str | None, Query(max_length=150)] = None,
    status: Annotated[str | None, Query(max_length=20)] = None,
    situacao_inpi: Annotated[str | None, Query(max_length=30)] = None,
) -> dict:
    """Organiza automaticamente a carteira pela situação oficial publicada na RPI."""
    situacao_inpi = _validar_grupo_situacao_inpi(situacao_inpi)
    grupo = _expressao_grupo_situacao_inpi()
    filtros = [ProcessoMonitorado.organizacao_id == usuario.organizacao_id]
    if status:
        filtros.append(ProcessoMonitorado.status == status)
    if situacao_inpi:
        filtros.append(grupo == situacao_inpi)
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

    contagens = dict(
        (
            await session.execute(
                select(grupo.label("grupo"), func.count())
                .select_from(ProcessoMonitorado)
                .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
                .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
                .where(*filtros)
                .group_by(grupo)
            )
        ).all()
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
    colunas = []
    for chave, titulo in GRUPOS_SITUACAO_INPI:
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
                    UsuarioOperacoes,
                    UsuarioOperacoes.id == ProcessoMonitorado.responsavel_id,
                )
                .where(*filtros, grupo == chave)
                .order_by(
                    ProcessoMonitorado.atualizado_em.desc(),
                    ProcessoMonitorado.id.desc(),
                )
                .limit(20)
            )
        ).all()
        itens = [
            {
                "id": monitorado.id,
                "numero": processo.numero,
                "titulo": processo.titulo,
                "titulo_exibicao": _titulo_exibicao(processo),
                "situacao": processo.situacao,
                "situacao_normalizada": processo.situacao_normalizada,
                "grupo_situacao_inpi": chave,
                "data_deposito": processo.data_deposito,
                "status": monitorado.status,
                "etapa_kanban": monitorado.etapa_kanban,
                "empresa": empresa_nome,
                "responsavel": responsavel_nome,
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
        ]
        total = int(contagens.get(chave, 0))
        colunas.append(
            {
                "chave": chave,
                "titulo": titulo,
                "total": total,
                "limite": 20,
                "tem_mais": total > len(itens),
                "itens": itens,
            }
        )
    return {
        "total": sum(int(valor) for valor in contagens.values()),
        "modo": "inpi",
        "fonte": "Situação consolidada a partir da última publicação na RPI",
        "colunas": colunas,
    }


@router.get("/empresas")
async def listar_empresas(
    session: SessionDep,
    usuario: ViewDep,
    busca: Annotated[str | None, Query(max_length=100)] = None,
) -> list[dict]:
    consulta = select(EmpresaCRM).where(EmpresaCRM.organizacao_id == usuario.organizacao_id)
    if busca:
        consulta = consulta.where(
            func.immutable_unaccent(func.lower(EmpresaCRM.nome)).ilike(f"%{_normalizar_busca(busca)}%")
        )
    empresas = (await session.execute(consulta.order_by(EmpresaCRM.nome).limit(100))).scalars().all()
    return [{"id": empresa.id, "nome": empresa.nome} for empresa in empresas]


@router.get("/responsaveis")
async def listar_responsaveis(session: SessionDep, usuario: ViewDep) -> list[dict]:
    pessoas = (
        (
            await session.execute(
                select(UsuarioOperacoes)
                .where(
                    UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
                    UsuarioOperacoes.ativo.is_(True),
                )
                .order_by(UsuarioOperacoes.nome)
            )
        )
        .scalars()
        .all()
    )
    return [{"id": pessoa.id, "nome": pessoa.nome} for pessoa in pessoas]


@router.get("/procuradores")
async def sugerir_procuradores(
    session: SessionDep,
    _usuario: ViewDep,
    busca: Annotated[str, Query(min_length=2, max_length=120)],
) -> list[str]:
    termo = f"%{_normalizar_busca(busca)}%"
    nomes = (
        (
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
        )
        .scalars()
        .all()
    )
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
                "titulo_exibicao": _titulo_exibicao(processo),
                "data_deposito": processo.data_deposito,
                "situacao": processo.situacao,
                "procurador": processo.procurador,
                "fonte": processo.fonte,
                "monitorado_id": monitorado_id,
            }
            for processo, monitorado_id in linhas
        ],
    }


def _titular_diverge(titular_informado: str, titulares_publicados: list[Titular]) -> bool:
    if not titulares_publicados:
        return False
    informado = _normalizar_busca(titular_informado)
    return not any(_normalizar_busca(titular.nome) in informado or informado in _normalizar_busca(titular.nome) for titular in titulares_publicados)


@router.post("/manual", status_code=201)
async def cadastrar_manual(
    dados: CadastroManual,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    numero_normalizado = normalizar_numero_processo(dados.numero)
    processo = (
        await session.execute(
            select(Processo)
            .where(Processo.numero_normalizado == numero_normalizado, Processo.tipo == TipoProcesso.MARCA)
            .options(selectinload(Processo.titulares))
        )
    ).scalar_one_or_none()
    if processo is None:
        # Processo ainda nao publicado na RPI: guarda a intencao e vincula
        # automaticamente assim que a sincronizacao semanal publicar o numero
        # (ver _vincular_pre_cadastros_pendentes em app/rpi/bulk_importer.py).
        pendente = (
            await session.execute(
                select(PreCadastroProcesso).where(
                    PreCadastroProcesso.organizacao_id == usuario.organizacao_id,
                    PreCadastroProcesso.numero_normalizado == numero_normalizado,
                    PreCadastroProcesso.status == "aguardando",
                )
            )
        ).scalar_one_or_none()
        if pendente is not None:
            return {
                "status": "pendente",
                "numero": dados.numero,
                "mensagem": "Esse processo já está aguardando publicação na RPI para esta organização.",
            }
        empresa = await _empresa(session, usuario, dados.empresa_id, dados.empresa_nome)
        await _validar_responsavel(session, usuario, dados.responsavel_id)
        session.add(
            PreCadastroProcesso(
                organizacao_id=usuario.organizacao_id,
                numero=dados.numero,
                numero_normalizado=numero_normalizado,
                titular=dados.titular,
                empresa_id=empresa.id if empresa else None,
                responsavel_id=dados.responsavel_id,
                observacoes=dados.observacoes,
                criado_por=usuario.ator,
            )
        )
        _auditar(
            session,
            request,
            usuario,
            "pre_cadastrar_processo",
            f"pre_cadastro:{numero_normalizado}",
            {"numero": dados.numero, "titular": dados.titular},
        )
        await session.commit()
        return {
            "status": "pendente",
            "numero": dados.numero,
            "mensagem": "Processo ainda não publicado na RPI. Cadastro salvo e será vinculado "
            "automaticamente à carteira assim que a RPI publicar este número.",
        }

    dados_vinculo = dados.model_copy()
    if _titular_diverge(dados.titular, processo.titulares):
        nota = f'Titular informado no cadastro ("{dados.titular}") não confere com o titular publicado na RPI — verifique.'
        dados_vinculo.observacoes = f"{dados_vinculo.observacoes}\n{nota}" if dados_vinculo.observacoes else nota

    # Achado FASE-A da auditoria do CRM (05/09/2026): checagem NAO BLOQUEANTE
    # de conflito de interesse antes de vincular o processo a um cliente --
    # o operador ve o aviso mas a vinculacao sempre prossegue; a auditoria
    # abaixo registra que o alerta foi levantado no momento da acao.
    empresa_alvo = await _empresa(session, usuario, dados.empresa_id, dados.empresa_nome)
    nomes_a_checar = [dados.titular, dados.empresa_nome, *(titular.nome for titular in processo.titulares)]
    alertas_conflito = await verificar_conflito_interesse(
        session, usuario.organizacao_id, nomes_a_checar, empresa_id_atual=empresa_alvo.id if empresa_alvo else None
    )
    if alertas_conflito:
        _auditar(
            session,
            request,
            usuario,
            "alerta_conflito",
            f"processo:{processo.id}",
            {"achados": alertas_conflito},
        )

    resultado = await _vincular_ids(session, request, usuario, [processo.id], dados_vinculo, origem="manual")
    return {**resultado, "numero": processo.numero, "status": "vinculado", "alertas_conflito_interesse": alertas_conflito}


@router.get("/pre-cadastros")
async def listar_pre_cadastros(
    session: SessionDep,
    usuario: ViewDep,
    status_filtro: Annotated[Literal["aguardando", "vinculado", "cancelado"] | None, Query(alias="status")] = (
        "aguardando"
    ),
) -> list[dict]:
    consulta = (
        select(PreCadastroProcesso)
        .where(PreCadastroProcesso.organizacao_id == usuario.organizacao_id)
        .order_by(PreCadastroProcesso.criado_em.desc())
        .limit(500)
    )
    if status_filtro:
        consulta = consulta.where(PreCadastroProcesso.status == status_filtro)
    itens = (await session.execute(consulta)).scalars().all()
    return [
        {
            "id": item.id,
            "numero": item.numero,
            "titular": item.titular,
            "empresa": item.empresa.nome if item.empresa else None,
            "responsavel_nome": item.responsavel.nome if item.responsavel else None,
            "observacoes": item.observacoes,
            "status": item.status,
            "titular_divergente": item.titular_divergente,
            "criado_por": item.criado_por,
            "criado_em": item.criado_em,
            "vinculado_em": item.vinculado_em,
            "processo_monitorado_id": item.processo_monitorado_id,
        }
        for item in itens
    ]


@router.post("/pre-cadastros/{pre_cadastro_id}/cancelar")
async def cancelar_pre_cadastro(
    pre_cadastro_id: int,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    pre_cadastro = (
        await session.execute(
            select(PreCadastroProcesso).where(
                PreCadastroProcesso.id == pre_cadastro_id,
                PreCadastroProcesso.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if pre_cadastro is None:
        raise HTTPException(404, "Pré-cadastro não encontrado")
    if pre_cadastro.status != "aguardando":
        raise HTTPException(409, "Este pré-cadastro já foi vinculado ou cancelado")
    pre_cadastro.status = "cancelado"
    _auditar(
        session,
        request,
        usuario,
        "cancelar_pre_cadastro",
        f"pre_cadastro:{pre_cadastro.numero_normalizado}",
        {"numero": pre_cadastro.numero},
    )
    await session.commit()
    return {"id": pre_cadastro.id, "status": pre_cadastro.status}


# Fragmentos procurados dentro do nome normalizado da coluna (casamento por conteúdo,
# não exato), para tolerar cabeçalhos como "Número do Processo", "Razão Social", etc.
COLUNAS_NUMERO = ("processo", "numero", "registro")
COLUNAS_EMPRESA = ("empresa", "cliente", "titular", "razaosocial")
COLUNAS_PROCURADOR = ("procurador", "agente", "escritorio")
COLUNAS_OBS = ("observ", "obs", "notas")
# Cabeçalhos curtos exatos que também identificam o número (ex.: "Nº" -> "no").
NUMERO_CURTO = {"no", "num", "n", "nprocesso"}


def _numero_processo(registro: dict[str, str]) -> str | None:
    numero = valor_coluna(registro, COLUNAS_NUMERO)
    if numero:
        return numero
    for chave, valor in registro.items():
        if chave in NUMERO_CURTO and valor.strip():
            return valor.strip()
    return None


@router.post("/importar", status_code=201)
async def importar_carteira(
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
    arquivo: Annotated[UploadFile, File()],
    responsavel_id: Annotated[int | None, Form()] = None,
) -> dict:
    """Importa uma carteira (CSV/XLSX) para os processos monitorados.

    Colunas reconhecidas (cabeçalho, sem acento/maiúsculas): numero (obrigatória),
    empresa, procurador, observacoes. Números não localizados na base RPI são
    reportados; duplicados já monitorados são ignorados.
    """
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(400, "Arquivo vazio.")
    if len(conteudo) > TAMANHO_MAXIMO_IMPORTACAO:
        raise HTTPException(413, "Arquivo muito grande (máximo 5 MB).")
    registros = ler_planilha(conteudo, arquivo.filename or "")
    if not registros:
        raise HTTPException(
            400,
            "Planilha vazia ou sem cabeçalho reconhecível. Inclua uma coluna 'numero'.",
        )
    await _validar_responsavel(session, usuario, responsavel_id)

    linhas_validas: list[tuple[str, str, dict[str, str]]] = []
    sem_numero = 0
    for registro in registros:
        numero = _numero_processo(registro)
        if not numero:
            sem_numero += 1
            continue
        linhas_validas.append((numero, normalizar_numero_processo(numero), registro))
    if not linhas_validas:
        colunas = ", ".join(chave for chave in registros[0] if chave) or "nenhuma"
        raise HTTPException(
            400,
            "Nenhuma coluna com número de processo foi reconhecida. "
            f"Colunas detectadas no arquivo: {colunas}. "
            "Renomeie a coluna do número para 'numero' ou 'processo'.",
        )

    normalizados = {norm for _, norm, _ in linhas_validas}
    processos = {
        row.numero_normalizado: row
        for row in (
            await session.execute(
                select(Processo).where(
                    Processo.numero_normalizado.in_(normalizados),
                    Processo.tipo == TipoProcesso.MARCA,
                )
            )
        ).scalars()
    }
    ja_monitorados = set(
        (
            await session.execute(
                select(ProcessoMonitorado.processo_id).where(
                    ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
                    ProcessoMonitorado.processo_id.in_(processo.id for processo in processos.values()),
                )
            )
        )
        .scalars()
        .all()
    )

    empresas_cache: dict[str, int | None] = {}
    processados: set[int] = set()
    vinculados = 0
    ja_vinculados = 0
    nao_encontrados: list[str] = []
    for numero, norm, registro in linhas_validas:
        processo = processos.get(norm)
        if processo is None:
            nao_encontrados.append(numero)
            continue
        if processo.id in ja_monitorados or processo.id in processados:
            ja_vinculados += 1
            continue
        empresa_nome = valor_coluna(registro, COLUNAS_EMPRESA)
        if empresa_nome and empresa_nome not in empresas_cache:
            empresa = await obter_ou_criar_empresa(session, usuario.organizacao_id, empresa_nome)
            empresas_cache[empresa_nome] = empresa.id if empresa else None
        session.add(
            ProcessoMonitorado(
                organizacao_id=usuario.organizacao_id,
                processo_id=processo.id,
                empresa_id=empresas_cache.get(empresa_nome) if empresa_nome else None,
                responsavel_id=responsavel_id,
                status="ativo",
                origem="importacao",
                procurador_origem=valor_coluna(registro, COLUNAS_PROCURADOR),
                observacoes=valor_coluna(registro, COLUNAS_OBS),
                vinculado_por=usuario.ator,
            )
        )
        processados.add(processo.id)
        vinculados += 1

    resultado = {
        "total_linhas": len(registros),
        "vinculados": vinculados,
        "ja_vinculados": ja_vinculados,
        "nao_encontrados": len(nao_encontrados),
        "sem_numero": sem_numero,
        "empresas_associadas": len([v for v in empresas_cache.values() if v]),
        "exemplos_nao_encontrados": nao_encontrados[:20],
    }
    _auditar(
        session,
        request,
        usuario,
        "importar_carteira",
        f"carteira:importacao:{arquivo.filename}",
        {k: v for k, v in resultado.items() if k != "exemplos_nao_encontrados"},
    )
    await session.commit()
    return resultado


@router.post("/vincular-lote", status_code=201)
async def vincular_lote(
    dados: VinculoLote,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    return await _vincular_ids(session, request, usuario, dados.processo_ids, dados, origem="selecao_procurador")


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
        )
        .scalars()
        .all()
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


@router.get("/{monitorado_id}/historico-kanban")
async def historico_kanban(
    monitorado_id: int,
    session: SessionDep,
    usuario: ViewDep,
) -> list[dict]:
    existe = await session.scalar(
        select(ProcessoMonitorado.id).where(
            ProcessoMonitorado.id == monitorado_id,
            ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
        )
    )
    if existe is None:
        raise HTTPException(404, "Processo monitorado não encontrado")
    eventos = (
        (
            await session.execute(
                select(HistoricoEtapaCarteira)
                .where(
                    HistoricoEtapaCarteira.organizacao_id == usuario.organizacao_id,
                    HistoricoEtapaCarteira.processo_monitorado_id == monitorado_id,
                )
                .order_by(HistoricoEtapaCarteira.criado_em.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": evento.id,
            "etapa_anterior": evento.etapa_anterior,
            "etapa_nova": evento.etapa_nova,
            "movido_por": evento.movido_por,
            "criado_em": evento.criado_em,
        }
        for evento in eventos
    ]


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
        "lead_id": monitorado.lead_id,
        "etapa_kanban": monitorado.etapa_kanban,
        "prioridade": monitorado.prioridade,
    }
    if dados.status is not None:
        monitorado.status = dados.status
    if dados.prioridade is not None:
        monitorado.prioridade = dados.prioridade
    if dados.empresa_id is not None or dados.empresa_nome:
        empresa = await _empresa(session, usuario, dados.empresa_id, dados.empresa_nome)
        monitorado.empresa_id = empresa.id if empresa else None
    if dados.remover_responsavel:
        monitorado.responsavel_id = None
    elif dados.responsavel_id is not None:
        await _validar_responsavel(session, usuario, dados.responsavel_id)
        monitorado.responsavel_id = dados.responsavel_id
    if dados.remover_lead:
        monitorado.lead_id = None
    elif dados.lead_id is not None:
        await _validar_lead(session, usuario, dados.lead_id)
        monitorado.lead_id = dados.lead_id
    if dados.observacoes is not None:
        monitorado.observacoes = dados.observacoes.strip() or None
    if dados.procurador is not None:
        # procurador é dado compartilhado do Processo (não do vínculo). Permitimos
        # corrigi-lo a partir da carteira porque muitas marcas do BADEPI vêm sem ele.
        processo = await session.get(Processo, monitorado.processo_id)
        if processo is not None:
            processo.procurador = dados.procurador or None
    if dados.etapa_kanban is not None and dados.etapa_kanban != monitorado.etapa_kanban:
        etapa_anterior = monitorado.etapa_kanban
        monitorado.etapa_kanban = dados.etapa_kanban
        monitorado.ordem_kanban = 0
        monitorado.etapa_atualizada_em = datetime.now(UTC)
        monitorado.etapa_atualizada_por = usuario.ator
        session.add(
            HistoricoEtapaCarteira(
                organizacao_id=usuario.organizacao_id,
                processo_monitorado_id=monitorado.id,
                etapa_anterior=etapa_anterior,
                etapa_nova=dados.etapa_kanban,
                movido_por=usuario.ator,
            )
        )
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
                "lead_id": monitorado.lead_id,
                "etapa_kanban": monitorado.etapa_kanban,
                "prioridade": monitorado.prioridade,
            },
        },
    )
    await session.commit()
    return {"status": "ok", "id": monitorado.id}


@router.post("/{monitorado_id}/relatorio-pdf")
async def gerar_relatorio_pdf(
    monitorado_id: int,
    dados: GeracaoRelatorioProcessoMonitorado,
    request: Request,
    session: SessionDep,
    usuario: ViewDep,
) -> Response:
    # Achado do usuário (08/09/2026): faltava uma forma de mostrar ao
    # cliente a fase atual do processo monitorado, sem dar acesso ao
    # sistema interno -- ver app.relatorios.gerar_pdf_processo_monitorado.
    linha = (
        await session.execute(
            select(ProcessoMonitorado, Processo, EmpresaCRM.nome, UsuarioOperacoes.nome)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
            .outerjoin(UsuarioOperacoes, UsuarioOperacoes.id == ProcessoMonitorado.responsavel_id)
            .where(
                ProcessoMonitorado.id == monitorado_id,
                ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
            )
        )
    ).first()
    if linha is None:
        raise HTTPException(404, "Processo monitorado não encontrado")
    monitorado, processo, empresa_nome, responsavel_nome = linha

    movimentacoes = (
        (
            await session.execute(
                select(Movimentacao)
                .where(Movimentacao.processo_id == processo.id)
                .order_by(Movimentacao.data_rpi.desc(), Movimentacao.id.desc())
            )
        )
        .scalars()
        .all()
    )
    titulares = (
        (
            await session.execute(
                select(Titular.nome)
                .join(processo_titulares, processo_titulares.c.titular_id == Titular.id)
                .where(processo_titulares.c.processo_id == processo.id)
            )
        )
        .scalars()
        .all()
    )

    pdf = gerar_pdf_processo_monitorado(
        {
            "numero": processo.numero,
            "titulo": processo.titulo,
            "tipo": processo.tipo,
            "data_deposito": processo.data_deposito,
            "situacao": processo.situacao,
            "titulares": list(titulares),
            "procurador": processo.procurador,
            "empresa": empresa_nome,
            "responsavel": responsavel_nome,
            "status": monitorado.status,
            "etapa_kanban_label": ETAPA_KANBAN_LABELS.get(monitorado.etapa_kanban, monitorado.etapa_kanban),
            "movimentacoes": [
                {"data_rpi": mov.data_rpi, "numero_rpi": mov.numero_rpi, "descricao": mov.descricao}
                for mov in movimentacoes
            ],
            "observacoes_relatorio": (dados.observacoes_relatorio or "").strip() or None,
            "gerado_em": datetime.now(UTC),
            "gerado_por": usuario.ator,
        }
    )
    _auditar(
        session,
        request,
        usuario,
        "gerar_relatorio_processo_monitorado",
        f"processo-monitorado:{monitorado.id}",
        {"processo": processo.numero},
    )
    await session.commit()
    nome_arquivo = normalizar_numero_processo(processo.numero) or "processo"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="acompanhamento-{nome_arquivo}.pdf"'},
    )


@router.post("/{monitorado_id}/atualizar")
async def atualizar_status_processo(
    monitorado_id: int,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    """Reconsolida a situação a partir dos despachos já sincronizados (feed RPI) e,
    se o processo ainda não tiver cliente, cadastra a empresa a partir do titular."""
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

    await consolidar_situacao(session, monitorado.processo_id)
    processo = await session.get(Processo, monitorado.processo_id)

    cliente_cadastrado = None
    if monitorado.empresa_id is None:
        titular_nome = (
            await session.execute(
                select(Titular.nome)
                .join(processo_titulares, processo_titulares.c.titular_id == Titular.id)
                .where(processo_titulares.c.processo_id == monitorado.processo_id)
                .order_by(Titular.nome)
                .limit(1)
            )
        ).scalar_one_or_none()
        empresa = await obter_ou_criar_empresa(session, usuario.organizacao_id, titular_nome)
        if empresa is not None:
            monitorado.empresa_id = empresa.id
            cliente_cadastrado = empresa.nome

    monitorado.atualizado_em = datetime.now(UTC)
    _auditar(
        session,
        request,
        usuario,
        "atualizar_status_carteira",
        f"processo-monitorado:{monitorado.id}",
        {
            "situacao": processo.situacao if processo else None,
            "cliente_cadastrado": cliente_cadastrado,
        },
    )
    await session.commit()
    return {
        "status": "ok",
        "situacao": processo.situacao if processo else None,
        "situacao_normalizada": processo.situacao_normalizada if processo else None,
        "relevancia_situacao": processo.relevancia_situacao if processo else None,
        "cliente_cadastrado": cliente_cadastrado,
    }

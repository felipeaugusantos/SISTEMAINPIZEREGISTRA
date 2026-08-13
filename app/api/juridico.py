import re
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.crm import obter_ou_criar_empresa
from app.database import get_session
from app.models import (
    EmpresaCRM,
    EventoAuditoria,
    EventoJuridico,
    ItemChecklistPrazo,
    Movimentacao,
    NotificacaoJuridica,
    PrazoJuridico,
    Processo,
    ProcessoMonitorado,
    Titular,
    UsuarioOperacoes,
    processo_titulares,
)
from app.proxy import cliente_ip

router = APIRouter(prefix="/v1/admin/juridico", tags=["operacao juridica"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("legal.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("legal.manage"))]

TIPOS_PRAZO = {
    "manifestacao": "Manifestação",
    "exigencia": "Cumprimento de exigência",
    "oposicao": "Oposição",
    "recurso": "Recurso",
    "pagamento": "Pagamento ou retribuição",
    "renovacao": "Renovação",
    "outro": "Outro",
}
STATUS_ATIVOS = {"aguardando_confirmacao", "pendente", "em_andamento"}
PADRAO_PRAZO = re.compile(
    # "prazo de 60 (sessenta) dias", "Prazo para cumprimento - 30 (Trinta) dias
    # corridos": o número aparece perto da palavra "prazo", antes de "dias",
    # sem cruzar o fim da frase.
    r"prazo\b[^.\n]{0,40}?(\d{1,3})\s*(?:\([^)]*\)\s*)?dias?",
    re.IGNORECASE,
)
# Prazo legal (dias corridos) por tipo de despacho de marca, aplicado quando o
# texto da publicação não soletra o número de dias. Ordem importa: o primeiro
# padrão que casar vence. Base: LPI (Lei 9.279/96); o prazo administrativo de
# marca é de 60 dias na quase totalidade dos casos. Despachos terminais
# (concessão, arquivamento, extinção, recurso julgado) não constam de propósito
# e não geram prazo.
DESPACHOS_PRAZO: tuple[tuple[re.Pattern[str], int, str, str], ...] = (
    (re.compile(r"instaura[çc][ãa]o de processo de nulidade", re.IGNORECASE),
     60, "manifestacao", "Manifestação em processo de nulidade"),
    (re.compile(r"notifica[çc][ãa]o de oposi[çc][ãa]o", re.IGNORECASE),
     60, "oposicao", "Manifestação sobre oposição"),
    (re.compile(r"para oposi[çc][ãa]o", re.IGNORECASE),
     60, "oposicao", "Janela de oposição"),
    (re.compile(r"notifica[çc][ãa]o de recurso", re.IGNORECASE),
     60, "recurso", "Contrarrazões de recurso"),
    (re.compile(r"indeferimento do pedido", re.IGNORECASE),
     60, "recurso", "Recurso contra indeferimento"),
    (re.compile(r"exig[êe]ncia", re.IGNORECASE),
     60, "exigencia", "Cumprimento de exigência"),
    (re.compile(r"deferimento do pedido", re.IGNORECASE),
     60, "pagamento", "Pagamento da taxa de concessão"),
)


def _classificar_despacho(descricao: str | None) -> tuple[int, str, str] | None:
    """Deriva (dias, tipo, ação) de uma movimentação de RPI de marca.

    O número soletrado no texto ("prazo de N dias") tem prioridade; na ausência
    dele, aplica-se o prazo legal do tipo de despacho. Retorna ``None`` para
    despachos terminais ou sem prazo processual mapeado.
    """
    texto = descricao or ""
    match = PADRAO_PRAZO.search(texto)
    dias_texto = int(match.group(1)) if match else None
    if dias_texto is not None and not 1 <= dias_texto <= 365:
        dias_texto = None
    for padrao, dias_legal, tipo, acao in DESPACHOS_PRAZO:
        if padrao.search(texto):
            return (dias_texto or dias_legal), tipo, acao
    if dias_texto is not None:
        return dias_texto, "outro", "Prazo indicado no texto da publicação"
    return None


def calcular_vencimento(data_base: date, dias: int, contagem: str) -> datetime:
    atual = data_base
    if contagem == "uteis":
        restantes = dias
        while restantes:
            atual += timedelta(days=1)
            if atual.weekday() < 5:
                restantes -= 1
    else:
        atual += timedelta(days=dias)
    return datetime.combine(atual, time(23, 59, 59), tzinfo=UTC)


class PrazoInput(BaseModel):
    processo_monitorado_id: int = Field(ge=1)
    titulo: str = Field(min_length=3, max_length=180)
    descricao: str | None = Field(default=None, max_length=4000)
    tipo: str = "manifestacao"
    data_base: date
    dias_prazo: int = Field(ge=0, le=3650)
    contagem: Literal["corridos", "uteis"] = "corridos"
    responsavel_id: int | None = Field(default=None, ge=1)
    escalonar_para_id: int | None = Field(default=None, ge=1)
    prioridade: Literal["baixa", "media", "alta", "critica"] = "media"
    antecedencia_dias: int = Field(default=7, ge=0, le=365)
    escalonar_dias_antes: int = Field(default=2, ge=0, le=365)

    @field_validator("tipo")
    @classmethod
    def validar_tipo(cls, value: str) -> str:
        if value not in TIPOS_PRAZO:
            raise ValueError("Tipo de prazo inválido")
        return value


class PrazoUpdate(BaseModel):
    status: (
        Literal["aguardando_confirmacao", "pendente", "em_andamento", "concluido", "cancelado"]
        | None
    ) = None
    responsavel_id: int | None = Field(default=None, ge=1)
    escalonar_para_id: int | None = Field(default=None, ge=1)
    prioridade: Literal["baixa", "media", "alta", "critica"] | None = None
    confirmar: bool = False
    descricao_evento: str | None = Field(default=None, max_length=500)


class EntregaInput(BaseModel):
    descricao: str = Field(min_length=3, max_length=500)
    protocolo: str | None = Field(default=None, max_length=120)
    documento: str | None = Field(default=None, max_length=500)


class ChecklistItemInput(BaseModel):
    descricao: str = Field(min_length=2, max_length=300)


class ChecklistItemUpdate(BaseModel):
    concluido: bool


# Checklist operacional padrão por tipo de prazo (LPI/prática de marcas).
CHECKLIST_PADRAO: dict[str, list[str]] = {
    "oposicao": [
        "Conferir prazo, marca e partes envolvidas",
        "Levantar fundamentos e anterioridades",
        "Elaborar a peça de oposição/manifestação",
        "Emitir e pagar a GRU",
        "Protocolar no INPI",
        "Arquivar o comprovante de protocolo",
    ],
    "recurso": [
        "Analisar o despacho de indeferimento",
        "Levantar os argumentos do recurso",
        "Elaborar as razões de recurso",
        "Emitir e pagar a GRU",
        "Protocolar o recurso no INPI",
        "Arquivar o comprovante",
    ],
    "exigencia": [
        "Ler o teor da exigência no parecer",
        "Reunir os documentos/correções exigidos",
        "Elaborar a petição de cumprimento",
        "Emitir e pagar a GRU (se aplicável)",
        "Protocolar o cumprimento no INPI",
        "Arquivar o comprovante",
    ],
    "pagamento": [
        "Emitir a GRU de concessão/retribuição",
        "Conferir o valor e o código de serviço",
        "Efetuar o pagamento dentro do prazo",
        "Protocolar o comprovante no INPI",
        "Arquivar o comprovante",
    ],
    "manifestacao": [
        "Conferir o objeto da manifestação",
        "Levantar subsídios e provas",
        "Elaborar a manifestação",
        "Protocolar no INPI",
        "Arquivar o comprovante",
    ],
}
CHECKLIST_GENERICO = [
    "Analisar o prazo e o processo",
    "Preparar a providência necessária",
    "Protocolar/registrar no INPI",
    "Arquivar o comprovante",
]


def _serializar_item_checklist(item: ItemChecklistPrazo) -> dict:
    return {
        "id": item.id,
        "descricao": item.descricao,
        "concluido": item.concluido,
        "ordem": item.ordem,
        "concluido_em": item.concluido_em,
        "concluido_por": item.concluido_por,
    }


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


def _evento(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    monitorado_id: int,
    tipo: str,
    descricao: str,
    prazo_id: int | None = None,
    detalhes: dict | None = None,
) -> None:
    session.add(
        EventoJuridico(
            organizacao_id=usuario.organizacao_id,
            prazo_id=prazo_id,
            processo_monitorado_id=monitorado_id,
            tipo=tipo,
            ator=usuario.ator,
            descricao=descricao[:500],
            detalhes=detalhes or {},
        )
    )


async def _usuario_valido(
    session: AsyncSession, organizacao_id: int, usuario_id: int | None
) -> bool:
    if usuario_id is None:
        return True
    resultado = await session.execute(
        select(UsuarioOperacoes.id).where(
            UsuarioOperacoes.id == usuario_id,
            UsuarioOperacoes.organizacao_id == organizacao_id,
            UsuarioOperacoes.ativo.is_(True),
        )
    )
    return bool(resultado.scalar_one_or_none())


async def _obter_prazo(
    session: AsyncSession, usuario: UsuarioAutenticado, prazo_id: int
) -> PrazoJuridico:
    prazo = (
        await session.execute(
            select(PrazoJuridico).where(
                PrazoJuridico.id == prazo_id,
                PrazoJuridico.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if prazo is None:
        raise HTTPException(404, "Prazo jurídico não encontrado")
    return prazo


def _dias_restantes(vencimento: datetime) -> int:
    agora = datetime.now(UTC)
    if vencimento.tzinfo is None:
        vencimento = vencimento.replace(tzinfo=UTC)
    return (vencimento.date() - agora.date()).days


def _serializar_prazo(row) -> dict:
    prazo, numero, marca, empresa, responsavel, escalacao = row
    restantes = _dias_restantes(prazo.vencimento_em)
    return {
        "id": prazo.id,
        "processo_monitorado_id": prazo.processo_monitorado_id,
        "numero": numero,
        "marca": marca,
        "empresa": empresa,
        "titulo": prazo.titulo,
        "descricao": prazo.descricao,
        "tipo": prazo.tipo,
        "tipo_nome": TIPOS_PRAZO.get(prazo.tipo, prazo.tipo),
        "origem": prazo.origem,
        "data_base": prazo.data_base,
        "dias_prazo": prazo.dias_prazo,
        "contagem": prazo.contagem,
        "vencimento_em": prazo.vencimento_em,
        "dias_restantes": restantes,
        "vencido": restantes < 0 and prazo.status in STATUS_ATIVOS,
        "status": prazo.status,
        "prioridade": prazo.prioridade,
        "confirmado": prazo.confirmado,
        "responsavel_id": prazo.responsavel_id,
        "responsavel": responsavel,
        "escalonar_para_id": prazo.escalonar_para_id,
        "escalonar_para": escalacao,
        "escalonado_em": prazo.escalonado_em,
    }


@router.get("/referencias")
async def referencias(session: SessionDep, usuario: ViewDep) -> dict:
    pessoas = (
        await session.execute(
            select(UsuarioOperacoes.id, UsuarioOperacoes.nome)
            .where(
                UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
                UsuarioOperacoes.ativo.is_(True),
            )
            .order_by(UsuarioOperacoes.nome)
        )
    ).all()
    processos = (
        await session.execute(
            select(ProcessoMonitorado.id, Processo.numero, Processo.titulo)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .where(
                ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
                ProcessoMonitorado.status == "ativo",
            )
            .order_by(Processo.titulo)
            .limit(1000)
        )
    ).all()
    return {
        "usuarios": [{"id": item.id, "nome": item.nome} for item in pessoas],
        "processos": [
            {"id": item.id, "nome": f"{item.numero} · {item.titulo or 'Sem título'}"}
            for item in processos
        ],
        "tipos": [{"id": key, "nome": value} for key, value in TIPOS_PRAZO.items()],
        "acoes": {"gerenciar": usuario.pode("legal.manage")},
    }


@router.get("/painel")
async def painel(
    session: SessionDep,
    usuario: ViewDep,
    busca: str | None = Query(default=None, max_length=150),
    status_prazo: str | None = Query(default=None, max_length=30),
    responsavel_id: int | None = Query(default=None, ge=1),
    inicio: date | None = None,
    fim: date | None = None,
) -> dict:
    filtros = [PrazoJuridico.organizacao_id == usuario.organizacao_id]
    if status_prazo:
        filtros.append(PrazoJuridico.status == status_prazo)
    if responsavel_id:
        filtros.append(PrazoJuridico.responsavel_id == responsavel_id)
    if inicio:
        filtros.append(PrazoJuridico.vencimento_em >= datetime.combine(inicio, time.min, UTC))
    if fim:
        filtros.append(PrazoJuridico.vencimento_em <= datetime.combine(fim, time.max, UTC))
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(
            or_(
                Processo.numero.ilike(termo),
                Processo.titulo.ilike(termo),
                EmpresaCRM.nome.ilike(termo),
                PrazoJuridico.titulo.ilike(termo),
            )
        )
    responsavel = UsuarioOperacoes.__table__.alias("responsavel")
    escalacao = UsuarioOperacoes.__table__.alias("escalacao")
    query = (
        select(
            PrazoJuridico,
            Processo.numero,
            Processo.titulo,
            EmpresaCRM.nome,
            responsavel.c.nome,
            escalacao.c.nome,
        )
        .join(ProcessoMonitorado, ProcessoMonitorado.id == PrazoJuridico.processo_monitorado_id)
        .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
        .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
        .outerjoin(responsavel, responsavel.c.id == PrazoJuridico.responsavel_id)
        .outerjoin(escalacao, escalacao.c.id == PrazoJuridico.escalonar_para_id)
        .where(*filtros)
        .order_by(PrazoJuridico.vencimento_em, PrazoJuridico.id)
        .limit(500)
    )
    itens = [_serializar_prazo(row) for row in (await session.execute(query)).all()]
    metricas = {
        "vencidos": sum(1 for item in itens if item["vencido"]),
        "vence_hoje": sum(
            1 for item in itens if item["dias_restantes"] == 0 and item["status"] in STATUS_ATIVOS
        ),
        "proximos_7_dias": sum(
            1
            for item in itens
            if 0 <= item["dias_restantes"] <= 7 and item["status"] in STATUS_ATIVOS
        ),
        "aguardando_confirmacao": sum(1 for item in itens if not item["confirmado"]),
        "total": len(itens),
    }
    notificacoes = (
        await session.execute(
            select(NotificacaoJuridica, PrazoJuridico, Processo.numero)
            .join(PrazoJuridico, PrazoJuridico.id == NotificacaoJuridica.prazo_id)
            .join(ProcessoMonitorado, ProcessoMonitorado.id == PrazoJuridico.processo_monitorado_id)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .where(
                NotificacaoJuridica.organizacao_id == usuario.organizacao_id,
                NotificacaoJuridica.status != "arquivada",
                or_(
                    NotificacaoJuridica.destinatario_id.is_(None),
                    NotificacaoJuridica.destinatario_id == usuario.id,
                ),
            )
            .order_by(NotificacaoJuridica.criado_em.desc())
            .limit(50)
        )
    ).all()
    historico = (
        await session.execute(
            select(EventoJuridico, Processo.numero)
            .join(
                ProcessoMonitorado, ProcessoMonitorado.id == EventoJuridico.processo_monitorado_id
            )
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .where(EventoJuridico.organizacao_id == usuario.organizacao_id)
            .order_by(EventoJuridico.criado_em.desc())
            .limit(100)
        )
    ).all()
    return {
        "metricas": metricas,
        "prazos": itens,
        "notificacoes": [
            {
                "id": item.id,
                "prazo_id": item.prazo_id,
                "numero": numero,
                "tipo": item.tipo,
                "titulo": item.titulo,
                "mensagem": item.mensagem,
                "status": item.status,
                "criado_em": item.criado_em,
            }
            for item, _prazo, numero in notificacoes
        ],
        "historico": [
            {
                "id": evento.id,
                "prazo_id": evento.prazo_id,
                "numero": numero,
                "tipo": evento.tipo,
                "ator": evento.ator,
                "descricao": evento.descricao,
                "detalhes": evento.detalhes,
                "criado_em": evento.criado_em,
            }
            for evento, numero in historico
        ],
        "acoes": {"gerenciar": usuario.pode("legal.manage")},
    }


@router.post("/prazos", status_code=status.HTTP_201_CREATED)
async def criar_prazo(
    dados: PrazoInput, request: Request, session: SessionDep, usuario: ManageDep
) -> dict:
    monitorado = (
        await session.execute(
            select(ProcessoMonitorado).where(
                ProcessoMonitorado.id == dados.processo_monitorado_id,
                ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if monitorado is None:
        raise HTTPException(404, "Processo monitorado não encontrado")
    for user_id in (dados.responsavel_id, dados.escalonar_para_id):
        if not await _usuario_valido(session, usuario.organizacao_id, user_id):
            raise HTTPException(404, "Responsável não encontrado")
    prazo = PrazoJuridico(
        organizacao_id=usuario.organizacao_id,
        processo_monitorado_id=dados.processo_monitorado_id,
        titulo=dados.titulo.strip(),
        descricao=dados.descricao.strip() if dados.descricao else None,
        tipo=dados.tipo,
        origem="manual",
        contagem=dados.contagem,
        data_base=dados.data_base,
        dias_prazo=dados.dias_prazo,
        vencimento_em=calcular_vencimento(dados.data_base, dados.dias_prazo, dados.contagem),
        status="pendente",
        prioridade=dados.prioridade,
        confirmado=True,
        responsavel_id=dados.responsavel_id,
        escalonar_para_id=dados.escalonar_para_id,
        antecedencia_dias=dados.antecedencia_dias,
        escalonar_dias_antes=dados.escalonar_dias_antes,
        criado_por=usuario.ator,
    )
    session.add(prazo)
    await session.flush()
    _evento(
        session,
        usuario,
        monitorado.id,
        "prazo_criado",
        "Prazo jurídico criado",
        prazo.id,
        {"vencimento": prazo.vencimento_em.isoformat()},
    )
    _auditar(
        session,
        request,
        usuario,
        "criar_prazo",
        f"prazo:{prazo.id}",
        {"processo_monitorado_id": monitorado.id},
    )
    await session.commit()
    return {"id": prazo.id, "vencimento_em": prazo.vencimento_em, "status": prazo.status}


@router.patch("/prazos/{prazo_id}")
async def atualizar_prazo(
    prazo_id: int,
    dados: PrazoUpdate,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    prazo = await _obter_prazo(session, usuario, prazo_id)
    for user_id in (dados.responsavel_id, dados.escalonar_para_id):
        if user_id and not await _usuario_valido(session, usuario.organizacao_id, user_id):
            raise HTTPException(404, "Responsável não encontrado")
    anterior = prazo.status
    if dados.confirmar:
        prazo.confirmado = True
        prazo.status = "pendente"
    if dados.status:
        prazo.status = dados.status
        if dados.status == "concluido":
            prazo.concluido_em = datetime.now(UTC)
            prazo.concluido_por = usuario.ator
    if dados.responsavel_id is not None:
        prazo.responsavel_id = dados.responsavel_id
    if dados.escalonar_para_id is not None:
        prazo.escalonar_para_id = dados.escalonar_para_id
    if dados.prioridade:
        prazo.prioridade = dados.prioridade
    descricao = dados.descricao_evento or f"Prazo atualizado de {anterior} para {prazo.status}"
    _evento(
        session,
        usuario,
        prazo.processo_monitorado_id,
        "prazo_atualizado",
        descricao,
        prazo.id,
        {"status_anterior": anterior, "status_atual": prazo.status},
    )
    _auditar(
        session, request, usuario, "atualizar_prazo", f"prazo:{prazo.id}", {"status": prazo.status}
    )
    await session.commit()
    return {"id": prazo.id, "status": prazo.status, "confirmado": prazo.confirmado}


@router.post("/prazos/{prazo_id}/entregas", status_code=status.HTTP_201_CREATED)
async def registrar_entrega(
    prazo_id: int,
    dados: EntregaInput,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    prazo = await _obter_prazo(session, usuario, prazo_id)
    detalhes = {"protocolo": dados.protocolo, "documento": dados.documento}
    _evento(
        session,
        usuario,
        prazo.processo_monitorado_id,
        "entrega_registrada",
        dados.descricao,
        prazo.id,
        detalhes,
    )
    _auditar(session, request, usuario, "registrar_entrega", f"prazo:{prazo.id}", detalhes)
    await session.commit()
    return {"registrado": True}


class VincularClienteInput(BaseModel):
    empresa_nome: str | None = Field(default=None, min_length=2, max_length=200)

    @field_validator("empresa_nome", mode="before")
    @classmethod
    def _limpar_nome(cls, valor: object) -> str | None:
        if valor is None:
            return None
        limpo = re.sub(r"\s+", " ", str(valor)).strip()
        return limpo or None


@router.post("/prazos/{prazo_id}/vincular-cliente")
async def vincular_cliente(
    prazo_id: int,
    dados: VincularClienteInput,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    """Vincula (ou cria) o cliente no CRM ao processo monitorado do prazo. Usa o
    nome informado ou, na ausência, o titular do processo."""
    prazo = await _obter_prazo(session, usuario, prazo_id)
    monitorado = await session.get(ProcessoMonitorado, prazo.processo_monitorado_id)
    if monitorado is None:
        raise HTTPException(404, "Processo monitorado não encontrado")
    nome = dados.empresa_nome
    if not nome:
        nome = (
            await session.execute(
                select(Titular.nome)
                .join(processo_titulares, processo_titulares.c.titular_id == Titular.id)
                .where(processo_titulares.c.processo_id == monitorado.processo_id)
                .order_by(Titular.nome)
                .limit(1)
            )
        ).scalar_one_or_none()
    empresa = await obter_ou_criar_empresa(session, usuario.organizacao_id, nome)
    if empresa is None:
        raise HTTPException(
            400, "Informe o nome do cliente (o processo não tem titular cadastrado)."
        )
    monitorado.empresa_id = empresa.id
    monitorado.atualizado_em = datetime.now(UTC)
    _auditar(
        session, request, usuario, "vincular_cliente_crm", f"prazo:{prazo.id}",
        {"empresa": empresa.nome},
    )
    await session.commit()
    return {"empresa": empresa.nome}


async def _obter_item_checklist(
    session: AsyncSession, usuario: UsuarioAutenticado, item_id: int
) -> ItemChecklistPrazo:
    item = (
        await session.execute(
            select(ItemChecklistPrazo).where(
                ItemChecklistPrazo.id == item_id,
                ItemChecklistPrazo.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Item de checklist não encontrado")
    return item


async def _listar_checklist(
    session: AsyncSession, usuario: UsuarioAutenticado, prazo_id: int
) -> dict:
    itens = (
        await session.execute(
            select(ItemChecklistPrazo)
            .where(
                ItemChecklistPrazo.prazo_id == prazo_id,
                ItemChecklistPrazo.organizacao_id == usuario.organizacao_id,
            )
            .order_by(ItemChecklistPrazo.ordem, ItemChecklistPrazo.id)
        )
    ).scalars().all()
    return {
        "itens": [_serializar_item_checklist(item) for item in itens],
        "total": len(itens),
        "concluidos": sum(1 for item in itens if item.concluido),
    }


@router.get("/checklists/resumo")
async def resumo_checklists(session: SessionDep, usuario: ViewDep) -> dict:
    """Progresso de checklist por prazo (para os cartões do Kanban)."""
    linhas = (
        await session.execute(
            select(
                ItemChecklistPrazo.prazo_id,
                func.count(),
                func.count().filter(ItemChecklistPrazo.concluido),
            )
            .where(ItemChecklistPrazo.organizacao_id == usuario.organizacao_id)
            .group_by(ItemChecklistPrazo.prazo_id)
        )
    ).all()
    return {
        str(prazo_id): {"total": total, "concluidos": feitos}
        for prazo_id, total, feitos in linhas
    }


@router.get("/prazos/{prazo_id}/checklist")
async def obter_checklist(prazo_id: int, session: SessionDep, usuario: ViewDep) -> dict:
    await _obter_prazo(session, usuario, prazo_id)
    return await _listar_checklist(session, usuario, prazo_id)


@router.post("/prazos/{prazo_id}/checklist", status_code=status.HTTP_201_CREATED)
async def adicionar_item_checklist(
    prazo_id: int, dados: ChecklistItemInput, session: SessionDep, usuario: ManageDep
) -> dict:
    await _obter_prazo(session, usuario, prazo_id)
    ordem = (
        await session.execute(
            select(func.coalesce(func.max(ItemChecklistPrazo.ordem), 0)).where(
                ItemChecklistPrazo.prazo_id == prazo_id
            )
        )
    ).scalar_one()
    session.add(
        ItemChecklistPrazo(
            organizacao_id=usuario.organizacao_id,
            prazo_id=prazo_id,
            descricao=dados.descricao,
            ordem=ordem + 1,
        )
    )
    await session.commit()
    return await _listar_checklist(session, usuario, prazo_id)


@router.post("/prazos/{prazo_id}/checklist/padrao", status_code=status.HTTP_201_CREATED)
async def aplicar_checklist_padrao(
    prazo_id: int, session: SessionDep, usuario: ManageDep
) -> dict:
    prazo = await _obter_prazo(session, usuario, prazo_id)
    existentes = (
        await session.execute(
            select(func.count())
            .select_from(ItemChecklistPrazo)
            .where(ItemChecklistPrazo.prazo_id == prazo_id)
        )
    ).scalar_one()
    if existentes:
        raise HTTPException(409, "Este prazo já possui itens de checklist.")
    modelo = CHECKLIST_PADRAO.get(prazo.tipo, CHECKLIST_GENERICO)
    for ordem, descricao in enumerate(modelo, start=1):
        session.add(
            ItemChecklistPrazo(
                organizacao_id=usuario.organizacao_id,
                prazo_id=prazo_id,
                descricao=descricao,
                ordem=ordem,
            )
        )
    _evento(
        session,
        usuario,
        prazo.processo_monitorado_id,
        "checklist_padrao",
        f"Checklist padrão aplicado ({len(modelo)} itens)",
        prazo.id,
    )
    await session.commit()
    return await _listar_checklist(session, usuario, prazo_id)


@router.patch("/checklist/{item_id}")
async def atualizar_item_checklist(
    item_id: int, dados: ChecklistItemUpdate, session: SessionDep, usuario: ManageDep
) -> dict:
    item = await _obter_item_checklist(session, usuario, item_id)
    item.concluido = dados.concluido
    item.concluido_em = datetime.now(UTC) if dados.concluido else None
    item.concluido_por = usuario.ator if dados.concluido else None
    await session.commit()
    return _serializar_item_checklist(item)


@router.delete("/checklist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_item_checklist(
    item_id: int, session: SessionDep, usuario: ManageDep
) -> None:
    item = await _obter_item_checklist(session, usuario, item_id)
    await session.delete(item)
    await session.commit()


@router.patch("/notificacoes/{notificacao_id}")
async def ler_notificacao(
    notificacao_id: int,
    request: Request,
    session: SessionDep,
    usuario: ViewDep,
) -> dict:
    item = (
        await session.execute(
            select(NotificacaoJuridica).where(
                NotificacaoJuridica.id == notificacao_id,
                NotificacaoJuridica.organizacao_id == usuario.organizacao_id,
                or_(
                    NotificacaoJuridica.destinatario_id.is_(None),
                    NotificacaoJuridica.destinatario_id == usuario.id,
                ),
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Notificação não encontrada")
    item.status = "lida"
    item.lida_em = datetime.now(UTC)
    item.lida_por = usuario.ator
    prazo = await _obter_prazo(session, usuario, item.prazo_id)
    _evento(
        session,
        usuario,
        prazo.processo_monitorado_id,
        "notificacao_lida",
        f"Notificação lida: {item.titulo}",
        prazo.id,
        {"notificacao_id": item.id},
    )
    _auditar(session, request, usuario, "ler_notificacao", f"notificacao:{item.id}", {})
    await session.commit()
    return {"id": item.id, "status": item.status, "lida_em": item.lida_em}


async def _notificar(
    session: AsyncSession,
    prazo: PrazoJuridico,
    tipo: str,
    destinatario_id: int | None,
    titulo: str,
    mensagem: str,
) -> bool:
    chave = f"juridico:{prazo.organizacao_id}:{prazo.id}:{tipo}:{destinatario_id or 0}"
    existe = (
        await session.execute(
            select(NotificacaoJuridica.id).where(NotificacaoJuridica.chave == chave)
        )
    ).scalar_one_or_none()
    if existe:
        return False
    session.add(
        NotificacaoJuridica(
            organizacao_id=prazo.organizacao_id,
            prazo_id=prazo.id,
            destinatario_id=destinatario_id,
            chave=chave,
            tipo=tipo,
            titulo=titulo,
            mensagem=mensagem,
            status="nova",
        )
    )
    return True


async def executar_motor_organizacao(
    session: AsyncSession, organizacao_id: int, ator: str = "motor-juridico"
) -> dict:
    """Materializa alertas e sugestões; usado pela API e pela rotina horária."""
    motor_usuario = SimpleNamespace(organizacao_id=organizacao_id, ator=ator)
    agora = datetime.now(UTC)
    prazos = (
        (
            await session.execute(
                select(PrazoJuridico).where(
                    PrazoJuridico.organizacao_id == organizacao_id,
                    PrazoJuridico.status.in_(STATUS_ATIVOS),
                    PrazoJuridico.confirmado.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    notificacoes = 0
    escalados = 0
    for prazo in prazos:
        dias = _dias_restantes(prazo.vencimento_em)
        if dias < 0:
            prazo.prioridade = "critica"
            notificacoes += await _notificar(
                session,
                prazo,
                "vencido",
                prazo.responsavel_id,
                "Prazo jurídico vencido",
                f"{prazo.titulo} venceu há {abs(dias)} dia(s).",
            )
        elif dias <= prazo.antecedencia_dias:
            notificacoes += await _notificar(
                session,
                prazo,
                f"antecedencia_{dias}",
                prazo.responsavel_id,
                "Prazo jurídico próximo",
                f"{prazo.titulo} vence em {dias} dia(s).",
            )
        if (
            dias <= prazo.escalonar_dias_antes
            and prazo.escalonar_para_id
            and prazo.escalonado_em is None
        ):
            prazo.escalonado_em = agora
            prazo.prioridade = "critica" if dias <= 0 else "alta"
            escalados += 1
            notificacoes += await _notificar(
                session,
                prazo,
                "escalonado",
                prazo.escalonar_para_id,
                "Prazo escalonado",
                f"{prazo.titulo} requer acompanhamento imediato.",
            )
            _evento(
                session,
                motor_usuario,
                prazo.processo_monitorado_id,
                "prazo_escalonado",
                "Prazo escalonado automaticamente",
                prazo.id,
                {"dias_restantes": dias},
            )

    candidatos = (
        await session.execute(
            select(ProcessoMonitorado, Movimentacao)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .join(Movimentacao, Movimentacao.processo_id == Processo.id)
            .outerjoin(
                PrazoJuridico,
                (PrazoJuridico.movimentacao_origem_id == Movimentacao.id)
                & (PrazoJuridico.organizacao_id == organizacao_id),
            )
            .where(
                ProcessoMonitorado.organizacao_id == organizacao_id,
                ProcessoMonitorado.status == "ativo",
                PrazoJuridico.id.is_(None),
            )
            .order_by(Movimentacao.data_rpi.desc())
            .limit(2000)
        )
    ).all()
    sugeridos = 0
    for monitorado, movimentacao in candidatos:
        if movimentacao.data_rpi is None:
            continue
        classificacao = _classificar_despacho(movimentacao.descricao)
        if classificacao is None:
            continue
        dias, tipo, acao = classificacao
        prazo = PrazoJuridico(
            organizacao_id=organizacao_id,
            processo_monitorado_id=monitorado.id,
            movimentacao_origem_id=movimentacao.id,
            responsavel_id=monitorado.responsavel_id,
            titulo=f"Revisar: {acao} (RPI {movimentacao.numero_rpi})"[:180],
            descricao=(movimentacao.descricao or "")[:4000],
            tipo=tipo,
            origem="motor_rpi",
            contagem="corridos",
            data_base=movimentacao.data_rpi,
            dias_prazo=dias,
            vencimento_em=calcular_vencimento(movimentacao.data_rpi, dias, "corridos"),
            status="aguardando_confirmacao",
            prioridade="alta",
            confirmado=False,
            criado_por="motor-juridico",
        )
        session.add(prazo)
        await session.flush()
        _evento(
            session,
            motor_usuario,
            monitorado.id,
            "prazo_sugerido",
            "Prazo sugerido pelo motor a partir do despacho da RPI; requer confirmação humana",
            prazo.id,
            {"movimentacao_id": movimentacao.id, "dias_prazo": dias, "tipo": tipo},
        )
        sugeridos += 1
    return {
        "notificacoes_criadas": notificacoes,
        "escalados": escalados,
        "prazos_sugeridos": sugeridos,
    }


@router.post("/motor/executar")
async def executar_motor(request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    resultado = await executar_motor_organizacao(session, usuario.organizacao_id, usuario.ator)
    _auditar(
        session,
        request,
        usuario,
        "executar_motor",
        "juridico:motor",
        resultado,
    )
    await session.commit()
    return resultado

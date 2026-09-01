from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.crm import REGRAS_AUTOMACAO, normalizar_empresa, obter_politica_crm
from app.database import get_session
from app.models import (
    Cadencia,
    CadenciaPasso,
    CanalContato,
    Contato,
    ContatoLead,
    EmpresaCRM,
    EventoAuditoria,
    Lead,
    LembreteCRM,
    PesquisaMarca,
    PoliticaCRM,
    Processo,
    ProcessoMonitorado,
    RegraAutomacao,
    StatusLead,
    UsuarioOperacoes,
)
from app.proxy import cliente_ip

router = APIRouter(prefix="/v1/admin/crm", tags=["crm"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CRMViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("crm.view"))]
CRMManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("crm.manage"))]
TIPOS_LEMBRETE = {
    "retorno": "Retornar contato",
    "atualizar_cadastro": "Atualizar cadastro",
    "enviar_proposta": "Enviar proposta",
    "cobrar_documentos": "Cobrar documentos",
    "acompanhar_processo": "Acompanhar processo",
    "outro": "Outro",
}
PRIORIDADES = {"baixa": "Baixa", "media": "Média", "alta": "Alta"}


def _escapar_busca(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _mascarar_email(email: str) -> str:
    local, _, dominio = email.partition("@")
    return f"{local[:1]}***@{dominio}" if dominio else "***"


def _mascarar_telefone(telefone: str) -> str:
    digitos = "".join(item for item in telefone if item.isdigit())
    return f"***{digitos[-4:]}" if digitos else "***"


def _mascarar_documento(documento: str | None) -> str | None:
    if not documento:
        return None
    digitos = "".join(item for item in documento if item.isdigit())
    return f"***{digitos[-4:]}" if digitos else "***"


def _serializar_historico(
    contato: ContatoLead,
    lead: Lead,
    marca: str | None,
    empresa: str | None,
    proximo_contato: datetime | None,
    usuario: UsuarioAutenticado,
) -> dict:
    pode_ver_pii = usuario.pode("leads.pii.view")
    return {
        "id": contato.id,
        "lead_id": lead.id,
        "cliente": lead.nome,
        "email": lead.email if pode_ver_pii else _mascarar_email(lead.email),
        "telefone": lead.telefone if pode_ver_pii else _mascarar_telefone(lead.telefone),
        "documento": (lead.documento if pode_ver_pii else _mascarar_documento(lead.documento)),
        "empresa_id": contato.empresa_id,
        "empresa": empresa or lead.empresa,
        "pesquisa_id": contato.pesquisa_id,
        "marca": marca,
        "status_cliente": lead.status,
        "canal": contato.canal,
        "resultado": contato.resultado,
        "observacao": contato.observacao,
        "proximo_contato": proximo_contato,
        "operador_id": contato.operador_id,
        "operador": contato.operador_nome,
        "criado_em": contato.criado_em,
    }


def _filtros(
    usuario: UsuarioAutenticado,
    busca: str | None,
    canal: CanalContato | None,
    operador_id: int | None,
    status_cliente: StatusLead | None,
    inicio: datetime | None,
    fim: datetime | None,
) -> list:
    filtros = [ContatoLead.organizacao_id == usuario.organizacao_id]
    if busca and busca.strip():
        termo = f"%{_escapar_busca(busca.strip())}%"
        campos = (
            Lead.nome,
            Lead.empresa,
            PesquisaMarca.marca,
            ContatoLead.resultado,
            ContatoLead.observacao,
            ContatoLead.operador_nome,
            EmpresaCRM.nome,
        )
        if usuario.pode("leads.pii.view"):
            campos += (Lead.email, Lead.telefone, Lead.documento)
        filtros.append(or_(*(campo.ilike(termo, escape="\\") for campo in campos)))
    if canal:
        filtros.append(ContatoLead.canal == canal)
    if operador_id:
        filtros.append(ContatoLead.operador_id == operador_id)
    if status_cliente:
        filtros.append(Lead.status == status_cliente)
    if inicio:
        filtros.append(ContatoLead.criado_em >= inicio)
    if fim:
        filtros.append(ContatoLead.criado_em <= fim)
    return filtros


@router.get("/historico")
async def historico_crm(
    session: SessionDep,
    usuario: CRMViewDep,
    busca: str | None = Query(default=None, max_length=120),
    canal: CanalContato | None = Query(default=None),
    operador_id: int | None = Query(default=None, ge=1),
    status_cliente: StatusLead | None = Query(default=None),
    inicio: datetime | None = Query(default=None),
    fim: datetime | None = Query(default=None),
    limite: int = Query(default=50, ge=1, le=200),
    deslocamento: int = Query(default=0, ge=0),
) -> dict:
    filtros = _filtros(usuario, busca, canal, operador_id, status_cliente, inicio, fim)
    proximo_contato = (
        select(func.min(LembreteCRM.lembrar_em))
        .where(
            LembreteCRM.lead_id == Lead.id,
            LembreteCRM.organizacao_id == usuario.organizacao_id,
            LembreteCRM.status == "pendente",
        )
        .correlate(Lead)
        .scalar_subquery()
    )
    base = (
        select(ContatoLead)
        .join(Lead, Lead.id == ContatoLead.lead_id)
        .outerjoin(PesquisaMarca, PesquisaMarca.id == ContatoLead.pesquisa_id)
        .outerjoin(EmpresaCRM, EmpresaCRM.id == ContatoLead.empresa_id)
    )
    total = (
        await session.execute(
            select(func.count())
            .select_from(ContatoLead)
            .join(Lead, Lead.id == ContatoLead.lead_id)
            .outerjoin(PesquisaMarca, PesquisaMarca.id == ContatoLead.pesquisa_id)
            .outerjoin(EmpresaCRM, EmpresaCRM.id == ContatoLead.empresa_id)
            .where(*filtros)
        )
    ).scalar_one()
    canais = dict(
        (
            await session.execute(
                select(ContatoLead.canal, func.count())
                .join(Lead, Lead.id == ContatoLead.lead_id)
                .outerjoin(PesquisaMarca, PesquisaMarca.id == ContatoLead.pesquisa_id)
                .outerjoin(EmpresaCRM, EmpresaCRM.id == ContatoLead.empresa_id)
                .where(*filtros)
                .group_by(ContatoLead.canal)
            )
        ).all()
    )
    linhas = (
        await session.execute(
            base.with_only_columns(ContatoLead, Lead, PesquisaMarca.marca, EmpresaCRM.nome, proximo_contato)
            .where(*filtros)
            .order_by(ContatoLead.criado_em.desc(), ContatoLead.id.desc())
            .offset(deslocamento)
            .limit(limite)
        )
    ).all()
    itens = []
    for linha in linhas:
        contato, lead, marca, empresa = linha[:4]
        proximo = linha[4] if len(linha) > 4 else None
        itens.append(_serializar_historico(contato, lead, marca, empresa, proximo, usuario))
    return {
        "total": int(total or 0),
        "deslocamento": deslocamento,
        "limite": limite,
        "tem_mais": deslocamento + len(itens) < int(total or 0),
        "por_canal": {str(canal): quantidade for canal, quantidade in canais.items()},
        "itens": itens,
        "acoes": {"registrar": usuario.pode("crm.manage")},
    }


@router.get("/referencias")
async def referencias_crm(session: SessionDep, usuario: CRMViewDep) -> dict:
    operadores = (
        await session.execute(
            select(UsuarioOperacoes.id, UsuarioOperacoes.nome)
            .where(
                UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
                UsuarioOperacoes.ativo.is_(True),
            )
            .order_by(UsuarioOperacoes.nome)
        )
    ).all()
    clientes = (
        await session.execute(
            select(Lead.id, Lead.nome, Lead.empresa, Lead.status)
            .where(
                Lead.organizacao_id == usuario.organizacao_id,
                Lead.arquivado_em.is_(None),
            )
            .order_by(Lead.nome)
            .limit(500)
        )
    ).all()
    return {
        "canais": [{"id": item.value, "nome": item.value.title()} for item in CanalContato],
        "operadores": [{"id": item.id, "nome": item.nome} for item in operadores],
        "status_clientes": [{"id": item.value, "nome": item.value.replace("_", " ").title()} for item in StatusLead],
        "tipos_lembrete": [{"id": chave, "nome": nome} for chave, nome in TIPOS_LEMBRETE.items()],
        "prioridades": [{"id": chave, "nome": nome} for chave, nome in PRIORIDADES.items()],
        "clientes": [
            {
                "id": item.id,
                "nome": item.nome,
                "empresa": item.empresa,
                "status": item.status,
            }
            for item in clientes
        ],
    }


class LembreteInput(BaseModel):
    lead_id: int = Field(ge=1)
    responsavel_id: int | None = Field(default=None, ge=1)
    tipo: Literal[
        "retorno",
        "atualizar_cadastro",
        "enviar_proposta",
        "cobrar_documentos",
        "acompanhar_processo",
        "outro",
    ]
    prioridade: Literal["baixa", "media", "alta"] = "media"
    titulo: str = Field(min_length=3, max_length=180)
    descricao: str | None = Field(default=None, max_length=2000)
    lembrar_em: datetime

    @field_validator("titulo", "descricao")
    @classmethod
    def limpar_texto(cls, valor: str | None) -> str | None:
        return valor.strip() or None if valor else valor


class LembreteUpdate(BaseModel):
    status: Literal["pendente", "concluido", "cancelado"] | None = None
    responsavel_id: int | None = Field(default=None, ge=1)
    prioridade: Literal["baixa", "media", "alta"] | None = None
    lembrar_em: datetime | None = None


def _auditar_lembrete(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    request: Request,
    acao: str,
    lembrete_id: int | None,
    detalhes: dict,
) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao=acao[:20],
            recurso=f"lembrete_crm:{lembrete_id or 'novo'}",
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes=detalhes,
        )
    )


async def _validar_cliente_responsavel(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    lead_id: int,
    responsavel_id: int | None,
) -> tuple[Lead, UsuarioOperacoes | None]:
    lead = (
        await session.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    responsavel = None
    if responsavel_id:
        responsavel = (
            await session.execute(
                select(UsuarioOperacoes).where(
                    UsuarioOperacoes.id == responsavel_id,
                    UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
                    UsuarioOperacoes.ativo.is_(True),
                )
            )
        ).scalar_one_or_none()
        if responsavel is None:
            raise HTTPException(status_code=422, detail="Responsável inválido")
    return lead, responsavel


def _serializar_lembrete(item: LembreteCRM) -> dict:
    return {
        "id": item.id,
        "lead_id": item.lead_id,
        "cliente": item.lead.nome,
        "empresa": item.lead.empresa,
        "responsavel_id": item.responsavel_id,
        "responsavel": item.responsavel.nome if item.responsavel else None,
        "tipo": item.tipo,
        "tipo_nome": TIPOS_LEMBRETE.get(item.tipo, item.tipo),
        "prioridade": item.prioridade,
        "titulo": item.titulo,
        "descricao": item.descricao,
        "lembrar_em": item.lembrar_em,
        "status": item.status,
        "vencido": item.status == "pendente" and item.lembrar_em < datetime.now(UTC),
        "criado_por": item.criado_por,
        "criado_em": item.criado_em,
        "concluido_em": item.concluido_em,
    }


@router.get("/lembretes")
async def listar_lembretes(
    session: SessionDep,
    usuario: CRMViewDep,
    status_lembrete: Literal["pendente", "concluido", "cancelado"] | None = Query(default="pendente", alias="status"),
    responsavel_id: int | None = Query(default=None, ge=1),
    tipo: str | None = Query(default=None, max_length=40),
    limite: int = Query(default=100, ge=1, le=200),
) -> dict:
    agora = datetime.now(UTC)
    em_7_dias = agora + timedelta(days=7)
    filtros = [LembreteCRM.organizacao_id == usuario.organizacao_id]
    if status_lembrete:
        filtros.append(LembreteCRM.status == status_lembrete)
    if responsavel_id:
        filtros.append(LembreteCRM.responsavel_id == responsavel_id)
    if tipo:
        filtros.append(LembreteCRM.tipo == tipo)
    itens = (
        (
            await session.execute(
                select(LembreteCRM).where(*filtros).order_by(LembreteCRM.lembrar_em, LembreteCRM.id).limit(limite)
            )
        )
        .scalars()
        .all()
    )
    alertas_atraso = [item for item in itens if item.status == "pendente" and item.lembrar_em < agora]
    metricas = (
        await session.execute(
            select(
                func.count().filter(LembreteCRM.status == "pendente", LembreteCRM.lembrar_em < agora),
                func.count().filter(
                    LembreteCRM.status == "pendente",
                    LembreteCRM.lembrar_em >= agora,
                    LembreteCRM.lembrar_em <= em_7_dias,
                ),
                func.count().filter(LembreteCRM.status == "pendente"),
            ).where(LembreteCRM.organizacao_id == usuario.organizacao_id)
        )
    ).one()
    limite_atualizacao = agora - timedelta(days=90)
    filtros_atualizacao = (
        Lead.organizacao_id == usuario.organizacao_id,
        Lead.arquivado_em.is_(None),
        Lead.atualizado_em < limite_atualizacao,
    )
    total_cadastros = (
        await session.execute(select(func.count()).select_from(Lead).where(*filtros_atualizacao))
    ).scalar_one()
    cadastros = (
        await session.execute(
            select(Lead.id, Lead.nome, Lead.empresa, Lead.atualizado_em)
            .where(*filtros_atualizacao)
            .order_by(Lead.atualizado_em)
            .limit(20)
        )
    ).all()
    return {
        "metricas": {
            "vencidos": int(metricas[0] or 0),
            "proximos_7_dias": int(metricas[1] or 0),
            "pendentes": int(metricas[2] or 0),
            "cadastros_para_atualizar": int(total_cadastros or 0),
        },
        "itens": [_serializar_lembrete(item) for item in itens],
        "alertas_atraso": [
            {
                **_serializar_lembrete(item),
                "tipo_alerta": "acao_atrasada",
                "gerado_em": agora,
            }
            for item in alertas_atraso
        ],
        "cadastros_para_atualizar": [
            {
                "lead_id": lead_id,
                "cliente": nome,
                "empresa": empresa,
                "atualizado_em": atualizado_em,
            }
            for lead_id, nome, empresa, atualizado_em in cadastros
        ],
        "acoes": {"gerenciar": usuario.pode("crm.manage")},
    }


@router.post("/lembretes", status_code=status.HTTP_201_CREATED)
async def criar_lembrete(
    dados: LembreteInput,
    request: Request,
    session: SessionDep,
    usuario: CRMManageDep,
) -> dict:
    lead, responsavel = await _validar_cliente_responsavel(session, usuario, dados.lead_id, dados.responsavel_id)
    item = LembreteCRM(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead.id,
        responsavel_id=dados.responsavel_id,
        tipo=dados.tipo,
        prioridade=dados.prioridade,
        titulo=dados.titulo,
        descricao=dados.descricao,
        lembrar_em=dados.lembrar_em,
        status="pendente",
        criado_por_id=usuario.id,
        criado_por=usuario.ator,
    )
    session.add(item)
    await session.flush()
    _auditar_lembrete(
        session,
        usuario,
        request,
        "criar_lembrete",
        item.id,
        {"lead_id": lead.id, "tipo": item.tipo, "lembrar_em": item.lembrar_em.isoformat()},
    )
    await session.commit()
    await session.refresh(item)
    item.lead = lead
    item.responsavel = responsavel
    return _serializar_lembrete(item)


@router.patch("/lembretes/{lembrete_id}")
async def atualizar_lembrete(
    lembrete_id: int,
    dados: LembreteUpdate,
    request: Request,
    session: SessionDep,
    usuario: CRMManageDep,
) -> dict:
    item = (
        await session.execute(
            select(LembreteCRM).where(
                LembreteCRM.id == lembrete_id,
                LembreteCRM.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Lembrete não encontrado")
    if "responsavel_id" in dados.model_fields_set:
        _, responsavel = await _validar_cliente_responsavel(session, usuario, item.lead_id, dados.responsavel_id)
        item.responsavel_id = dados.responsavel_id
        item.responsavel = responsavel
    if dados.prioridade is not None:
        item.prioridade = dados.prioridade
    if dados.lembrar_em is not None:
        item.lembrar_em = dados.lembrar_em
    if dados.status is not None:
        item.status = dados.status
        if dados.status == "concluido":
            item.concluido_em = datetime.now(UTC)
            item.concluido_por = usuario.ator
        elif dados.status == "pendente":
            item.concluido_em = None
            item.concluido_por = None
    _auditar_lembrete(
        session,
        usuario,
        request,
        "alterar_lembrete",
        item.id,
        dados.model_dump(exclude_unset=True, mode="json"),
    )
    await session.commit()
    await session.refresh(item)
    return _serializar_lembrete(item)


# --- Separação empresa/contato (Terceira entrega, item 1) -------------------


class EmpresaUpdate(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=200)
    documento: str | None = Field(default=None, max_length=18)
    segmento: str | None = Field(default=None, max_length=80)
    telefone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=254)
    site: str | None = Field(default=None, max_length=200)
    observacoes: str | None = Field(default=None, max_length=2000)


class ContatoInput(BaseModel):
    nome: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=254)
    telefone: str | None = Field(default=None, max_length=30)
    cargo: str | None = Field(default=None, max_length=80)
    principal: bool = False
    observacoes: str | None = Field(default=None, max_length=2000)


def _contato_dict(c: Contato) -> dict:
    return {
        "id": c.id,
        "empresa_id": c.empresa_id,
        "nome": c.nome,
        "email": c.email,
        "telefone": c.telefone,
        "cargo": c.cargo,
        "principal": c.principal,
        "observacoes": c.observacoes,
    }


async def _empresa_da_org(session: AsyncSession, empresa_id: int, organizacao_id: int) -> EmpresaCRM:
    empresa = (
        await session.execute(
            select(EmpresaCRM).where(EmpresaCRM.id == empresa_id, EmpresaCRM.organizacao_id == organizacao_id)
        )
    ).scalar_one_or_none()
    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return empresa


@router.get("/empresas/{empresa_id}")
async def obter_empresa(empresa_id: int, session: SessionDep, usuario: CRMViewDep) -> dict:
    # Visão 360º: além dos dados cadastrais e contatos, traz todas as
    # oportunidades e todos os processos monitorados desta empresa -- antes
    # cada um só aparecia navegando lead por lead ou na carteira jurídica,
    # sem nenhuma tela que juntasse os dois lados do relacionamento.
    empresa = await _empresa_da_org(session, empresa_id, usuario.organizacao_id)
    contatos = (
        (
            await session.execute(
                select(Contato)
                .where(
                    Contato.empresa_id == empresa_id,
                    Contato.organizacao_id == usuario.organizacao_id,
                )
                .order_by(Contato.principal.desc(), Contato.nome)
            )
        )
        .scalars()
        .all()
    )
    leads = (
        await session.execute(
            select(Lead, UsuarioOperacoes.nome)
            .outerjoin(UsuarioOperacoes, UsuarioOperacoes.id == Lead.responsavel_id)
            .where(
                Lead.empresa_id == empresa_id,
                Lead.organizacao_id == usuario.organizacao_id,
                Lead.arquivado_em.is_(None),
            )
            .order_by(Lead.criado_em.desc())
        )
    ).all()
    processos = (
        await session.execute(
            select(ProcessoMonitorado, Processo)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .where(
                ProcessoMonitorado.empresa_id == empresa_id,
                ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
            )
            .order_by(ProcessoMonitorado.atualizado_em.desc())
        )
    ).all()
    return {
        "id": empresa.id,
        "nome": empresa.nome,
        "documento": empresa.documento,
        "segmento": empresa.segmento,
        "telefone": empresa.telefone,
        "email": empresa.email,
        "site": empresa.site,
        "observacoes": empresa.observacoes,
        "contatos": [_contato_dict(c) for c in contatos],
        "leads": [
            {
                "id": lead.id,
                "marca": lead.marca,
                "status": lead.status.value,
                "fase": lead.fase,
                "responsavel_nome": responsavel_nome,
                "proxima_acao_em": lead.proxima_acao_em,
                "criado_em": lead.criado_em,
            }
            for lead, responsavel_nome in leads
        ],
        "processos": [
            {
                "id": monitorado.id,
                "numero": processo.numero,
                "titulo": processo.titulo,
                "situacao": processo.situacao,
                "etapa_kanban": monitorado.etapa_kanban,
                "status": monitorado.status,
                "lead_id": monitorado.lead_id,
            }
            for monitorado, processo in processos
        ],
    }


@router.put("/empresas/{empresa_id}")
async def editar_empresa(
    empresa_id: int,
    dados: EmpresaUpdate,
    request: Request,
    session: SessionDep,
    usuario: CRMManageDep,
) -> dict:
    empresa = await _empresa_da_org(session, empresa_id, usuario.organizacao_id)
    campos = dados.model_dump(exclude_unset=True)
    if "nome" in campos and campos["nome"]:
        novo_nome = campos["nome"].strip()
        normalizado = normalizar_empresa(novo_nome)
        duplicada = (
            await session.execute(
                select(EmpresaCRM.id).where(
                    EmpresaCRM.organizacao_id == usuario.organizacao_id,
                    EmpresaCRM.nome_normalizado == normalizado,
                    EmpresaCRM.id != empresa_id,
                )
            )
        ).scalar_one_or_none()
        if duplicada:
            raise HTTPException(status_code=409, detail="Já existe uma empresa com esse nome")
        empresa.nome = novo_nome
        empresa.nome_normalizado = normalizado
        campos.pop("nome")
    for campo, valor in campos.items():
        setattr(empresa, campo, valor or None)
    await session.commit()
    return {"id": empresa.id, "status": "atualizada"}


@router.post("/empresas/{empresa_id}/contatos", status_code=status.HTTP_201_CREATED)
async def criar_contato(empresa_id: int, dados: ContatoInput, session: SessionDep, usuario: CRMManageDep) -> dict:
    await _empresa_da_org(session, empresa_id, usuario.organizacao_id)
    if dados.principal:
        await session.execute(
            Contato.__table__.update()
            .where(Contato.empresa_id == empresa_id, Contato.organizacao_id == usuario.organizacao_id)
            .values(principal=False)
        )
    contato = Contato(
        organizacao_id=usuario.organizacao_id,
        empresa_id=empresa_id,
        nome=dados.nome.strip(),
        email=dados.email or None,
        telefone=dados.telefone or None,
        cargo=dados.cargo or None,
        principal=dados.principal,
        observacoes=dados.observacoes or None,
    )
    session.add(contato)
    await session.commit()
    return {"id": contato.id}


async def _contato_da_org(session: AsyncSession, contato_id: int, organizacao_id: int) -> Contato:
    contato = (
        await session.execute(select(Contato).where(Contato.id == contato_id, Contato.organizacao_id == organizacao_id))
    ).scalar_one_or_none()
    if contato is None:
        raise HTTPException(status_code=404, detail="Contato não encontrado")
    return contato


@router.put("/contatos/{contato_id}")
async def editar_contato(contato_id: int, dados: ContatoInput, session: SessionDep, usuario: CRMManageDep) -> dict:
    contato = await _contato_da_org(session, contato_id, usuario.organizacao_id)
    if dados.principal and not contato.principal:
        await session.execute(
            Contato.__table__.update()
            .where(
                Contato.empresa_id == contato.empresa_id,
                Contato.organizacao_id == usuario.organizacao_id,
            )
            .values(principal=False)
        )
    contato.nome = dados.nome.strip()
    contato.email = dados.email or None
    contato.telefone = dados.telefone or None
    contato.cargo = dados.cargo or None
    contato.principal = dados.principal
    contato.observacoes = dados.observacoes or None
    await session.commit()
    return {"id": contato.id, "status": "atualizado"}


@router.delete("/contatos/{contato_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_contato(contato_id: int, session: SessionDep, usuario: CRMManageDep):
    contato = await _contato_da_org(session, contato_id, usuario.organizacao_id)
    await session.delete(contato)
    await session.commit()
    return None


# --- Automações (Terceira entrega, item 2a) --------------------------------


class AutomacaoUpdate(BaseModel):
    ativo: bool
    dias: int = Field(ge=0, le=180)


class PoliticaCRMUpdate(BaseModel):
    exigir_responsavel: bool = True
    atribuir_ao_operador: bool = False
    exigir_proxima_acao: bool = True
    dias_proxima_acao_padrao: int | None = Field(default=None, ge=0, le=365)


def _politica_dict(politica: PoliticaCRM) -> dict:
    return {
        "exigir_responsavel": politica.exigir_responsavel,
        "atribuir_ao_operador": politica.atribuir_ao_operador,
        "exigir_proxima_acao": politica.exigir_proxima_acao,
        "dias_proxima_acao_padrao": politica.dias_proxima_acao_padrao,
        "atualizado_por": politica.atualizado_por,
        "atualizado_em": politica.atualizado_em,
    }


@router.get("/politica")
async def consultar_politica_crm(session: SessionDep, usuario: CRMViewDep) -> dict:
    return _politica_dict(await obter_politica_crm(session, usuario.organizacao_id))


@router.put("/politica")
async def editar_politica_crm(dados: PoliticaCRMUpdate, session: SessionDep, usuario: CRMManageDep) -> dict:
    politica = (
        await session.execute(select(PoliticaCRM).where(PoliticaCRM.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if politica is None:
        politica = PoliticaCRM(organizacao_id=usuario.organizacao_id)
        session.add(politica)
    for campo, valor in dados.model_dump().items():
        setattr(politica, campo, valor)
    politica.atualizado_por = usuario.ator
    await session.commit()
    return _politica_dict(politica)


@router.get("/automacoes")
async def listar_automacoes(session: SessionDep, usuario: CRMViewDep) -> dict:
    overrides = {
        row.chave: row
        for row in (
            await session.execute(select(RegraAutomacao).where(RegraAutomacao.organizacao_id == usuario.organizacao_id))
        ).scalars()
    }
    itens = []
    for chave, regra in REGRAS_AUTOMACAO.items():
        ov = overrides.get(chave)
        itens.append(
            {
                "chave": chave,
                "label": regra["label"],
                "evento": regra["evento"],
                "gatilho": regra["gatilho"],
                "titulo": regra["titulo"],
                "prioridade": regra["prioridade"],
                "ativo": ov.ativo if ov else True,
                "dias": ov.dias if ov else regra["dias"],
            }
        )
    return {"itens": itens}


@router.put("/automacoes/{chave}")
async def editar_automacao(chave: str, dados: AutomacaoUpdate, session: SessionDep, usuario: CRMManageDep) -> dict:
    if chave not in REGRAS_AUTOMACAO:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    regra = (
        await session.execute(
            select(RegraAutomacao).where(
                RegraAutomacao.organizacao_id == usuario.organizacao_id,
                RegraAutomacao.chave == chave,
            )
        )
    ).scalar_one_or_none()
    if regra is None:
        regra = RegraAutomacao(organizacao_id=usuario.organizacao_id, chave=chave)
        session.add(regra)
    regra.ativo = dados.ativo
    regra.dias = dados.dias
    await session.commit()
    return {"chave": chave, "ativo": regra.ativo, "dias": regra.dias}


# --- Cadências (Terceira entrega, item 2b) ---------------------------------

CANAIS_CADENCIA = ("email", "whatsapp", "ligacao", "reuniao", "outro")


class PassoInput(BaseModel):
    dia: int = Field(ge=0, le=365)
    canal: str = Field(default="outro", max_length=20)
    titulo: str = Field(min_length=1, max_length=180)
    descricao: str | None = Field(default=None, max_length=2000)


class CadenciaInput(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    descricao: str | None = Field(default=None, max_length=2000)
    ativo: bool = True
    passos: list[PassoInput] = Field(default_factory=list, max_length=30)


def _cadencia_dict(c: Cadencia) -> dict:
    return {
        "id": c.id,
        "nome": c.nome,
        "descricao": c.descricao,
        "ativo": c.ativo,
        "passos": [
            {
                "id": p.id,
                "dia": p.dia,
                "canal": p.canal,
                "titulo": p.titulo,
                "descricao": p.descricao,
            }
            for p in c.passos
        ],
    }


@router.get("/cadencias")
async def listar_cadencias(session: SessionDep, usuario: CRMViewDep) -> dict:
    cads = (
        (
            await session.execute(
                select(Cadencia)
                .where(Cadencia.organizacao_id == usuario.organizacao_id)
                .options(selectinload(Cadencia.passos))
                .order_by(Cadencia.ativo.desc(), Cadencia.nome)
            )
        )
        .scalars()
        .all()
    )
    return {"itens": [_cadencia_dict(c) for c in cads]}


def _montar_passos(cadencia: Cadencia, passos: list[PassoInput], organizacao_id: int) -> None:
    for i, p in enumerate(passos):
        canal = p.canal if p.canal in CANAIS_CADENCIA else "outro"
        cadencia.passos.append(
            CadenciaPasso(
                organizacao_id=organizacao_id,
                ordem=i,
                dia=p.dia,
                canal=canal,
                titulo=p.titulo.strip(),
                descricao=p.descricao or None,
            )
        )


@router.post("/cadencias", status_code=status.HTTP_201_CREATED)
async def criar_cadencia(dados: CadenciaInput, session: SessionDep, usuario: CRMManageDep) -> dict:
    cadencia = Cadencia(
        organizacao_id=usuario.organizacao_id,
        nome=dados.nome.strip(),
        descricao=dados.descricao or None,
        ativo=dados.ativo,
    )
    _montar_passos(cadencia, dados.passos, usuario.organizacao_id)
    session.add(cadencia)
    await session.commit()
    return {"id": cadencia.id}


@router.put("/cadencias/{cadencia_id}")
async def editar_cadencia(cadencia_id: int, dados: CadenciaInput, session: SessionDep, usuario: CRMManageDep) -> dict:
    cadencia = (
        await session.execute(
            select(Cadencia)
            .where(Cadencia.id == cadencia_id, Cadencia.organizacao_id == usuario.organizacao_id)
            .options(selectinload(Cadencia.passos))
        )
    ).scalar_one_or_none()
    if cadencia is None:
        raise HTTPException(status_code=404, detail="Cadência não encontrada")
    cadencia.nome = dados.nome.strip()
    cadencia.descricao = dados.descricao or None
    cadencia.ativo = dados.ativo
    cadencia.passos.clear()
    await session.flush()
    _montar_passos(cadencia, dados.passos, usuario.organizacao_id)
    await session.commit()
    return {"id": cadencia.id, "status": "atualizada"}


@router.delete("/cadencias/{cadencia_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_cadencia(cadencia_id: int, session: SessionDep, usuario: CRMManageDep):
    cadencia = (
        await session.execute(
            select(Cadencia).where(Cadencia.id == cadencia_id, Cadencia.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if cadencia is None:
        raise HTTPException(status_code=404, detail="Cadência não encontrada")
    await session.delete(cadencia)
    await session.commit()
    return None

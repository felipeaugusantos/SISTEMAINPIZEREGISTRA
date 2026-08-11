from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import CanalContato, ContatoLead, EmpresaCRM, Lead, PesquisaMarca, UsuarioOperacoes

router = APIRouter(prefix="/v1/admin/crm", tags=["crm"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CRMViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]


def _escapar_busca(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _mascarar_email(email: str) -> str:
    local, _, dominio = email.partition("@")
    return f"{local[:1]}***@{dominio}" if dominio else "***"


def _mascarar_telefone(telefone: str) -> str:
    digitos = "".join(item for item in telefone if item.isdigit())
    return f"***{digitos[-4:]}" if digitos else "***"


def _serializar_historico(
    contato: ContatoLead,
    lead: Lead,
    marca: str | None,
    empresa: str | None,
    usuario: UsuarioAutenticado,
) -> dict:
    pode_ver_pii = usuario.pode("leads.pii.view")
    return {
        "id": contato.id,
        "lead_id": lead.id,
        "cliente": lead.nome,
        "email": lead.email if pode_ver_pii else _mascarar_email(lead.email),
        "telefone": lead.telefone if pode_ver_pii else _mascarar_telefone(lead.telefone),
        "empresa_id": contato.empresa_id,
        "empresa": empresa or lead.empresa,
        "pesquisa_id": contato.pesquisa_id,
        "marca": marca,
        "canal": contato.canal,
        "resultado": contato.resultado,
        "observacao": contato.observacao,
        "operador_id": contato.operador_id,
        "operador": contato.operador_nome,
        "criado_em": contato.criado_em,
    }


def _filtros(
    usuario: UsuarioAutenticado,
    busca: str | None,
    canal: CanalContato | None,
    operador_id: int | None,
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
        )
        if usuario.pode("leads.pii.view"):
            campos += (Lead.email, Lead.telefone)
        filtros.append(or_(*(campo.ilike(termo, escape="\\") for campo in campos)))
    if canal:
        filtros.append(ContatoLead.canal == canal)
    if operador_id:
        filtros.append(ContatoLead.operador_id == operador_id)
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
    inicio: datetime | None = Query(default=None),
    fim: datetime | None = Query(default=None),
    limite: int = Query(default=50, ge=1, le=200),
    deslocamento: int = Query(default=0, ge=0),
) -> dict:
    filtros = _filtros(usuario, busca, canal, operador_id, inicio, fim)
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
            .where(*filtros)
        )
    ).scalar_one()
    canais = dict(
        (
            await session.execute(
                select(ContatoLead.canal, func.count())
                .join(Lead, Lead.id == ContatoLead.lead_id)
                .outerjoin(PesquisaMarca, PesquisaMarca.id == ContatoLead.pesquisa_id)
                .where(*filtros)
                .group_by(ContatoLead.canal)
            )
        ).all()
    )
    linhas = (
        await session.execute(
            base.with_only_columns(ContatoLead, Lead, PesquisaMarca.marca, EmpresaCRM.nome)
            .where(*filtros)
            .order_by(ContatoLead.criado_em.desc(), ContatoLead.id.desc())
            .offset(deslocamento)
            .limit(limite)
        )
    ).all()
    itens = [
        _serializar_historico(contato, lead, marca, empresa, usuario)
        for contato, lead, marca, empresa in linhas
    ]
    return {
        "total": int(total or 0),
        "deslocamento": deslocamento,
        "limite": limite,
        "tem_mais": deslocamento + len(itens) < int(total or 0),
        "por_canal": {str(canal): quantidade for canal, quantidade in canais.items()},
        "itens": itens,
        "acoes": {"registrar": usuario.pode("leads.manage")},
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
    return {
        "canais": [{"id": item.value, "nome": item.value.title()} for item in CanalContato],
        "operadores": [{"id": item.id, "nome": item.nome} for item in operadores],
    }

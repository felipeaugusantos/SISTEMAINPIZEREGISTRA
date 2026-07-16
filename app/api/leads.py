import csv
import io
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Lead, StatusLead
from app.ratelimit import RateLimiter
from app.schemas import LeadCreate, LeadListResponse, LeadResponse, LeadStatusUpdate
from app.settings import get_settings

router = APIRouter(tags=["leads"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
security = HTTPBasic(auto_error=False)

limitar_leads = RateLimiter(limite=10, janela_segundos=60)
limitar_admin = RateLimiter(limite=20, janela_segundos=60)


def exigir_admin(
    credenciais: Annotated[HTTPBasicCredentials | None, Depends(security)],
    _: Annotated[None, Depends(limitar_admin)] = None,
) -> str:
    settings = get_settings()
    usuario_correto = credenciais is not None and secrets.compare_digest(
        credenciais.username.encode(), settings.admin_username.encode()
    )
    senha_correta = credenciais is not None and secrets.compare_digest(
        credenciais.password.encode(), settings.admin_password.encode()
    )
    if not usuario_correto or not senha_correta:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais administrativas inválidas",
            headers={"WWW-Authenticate": 'Basic realm="Painel de leads"'},
        )
    return credenciais.username


AdminDep = Annotated[str, Depends(exigir_admin)]
BuscaLead = Annotated[str | None, Query(max_length=100)]
StatusLeadFiltro = Annotated[StatusLead | None, Query(alias="status")]
LimiteLead = Annotated[int, Query(ge=1, le=200)]
DeslocamentoLead = Annotated[int, Query(ge=0)]


@router.post(
    "/v1/leads",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limitar_leads)],
)
async def criar_lead(dados: LeadCreate, session: SessionDep) -> Lead:
    if dados.website:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Envio inválido")

    desde = datetime.now(UTC) - timedelta(minutes=15)
    consulta_existente = (
        select(Lead)
        .where(
            func.lower(Lead.email) == dados.email.lower(),
            Lead.telefone == dados.telefone,
            func.lower(Lead.marca) == dados.marca.lower(),
            Lead.processo_numero == dados.processo_numero,
            Lead.criado_em >= desde,
        )
        .order_by(Lead.criado_em.desc())
        .limit(1)
    )
    existente = (await session.execute(consulta_existente)).scalar_one_or_none()
    if existente is not None:
        return existente

    lead = Lead(
        nome=dados.nome,
        email=dados.email.lower(),
        telefone=dados.telefone,
        marca=dados.marca,
        processo_numero=dados.processo_numero,
        origem=dados.origem,
        tipo_interesse=dados.tipo_interesse,
        aceite_privacidade=True,
        status=StatusLead.NOVO,
    )
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


@router.get("/v1/admin/leads", response_model=LeadListResponse)
async def listar_leads(
    session: SessionDep,
    _: AdminDep,
    busca: BuscaLead = None,
    status_lead: StatusLeadFiltro = None,
    limite: LimiteLead = 50,
    deslocamento: DeslocamentoLead = 0,
) -> LeadListResponse:
    filtros = []
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(
            or_(
                Lead.nome.ilike(termo),
                Lead.email.ilike(termo),
                Lead.telefone.ilike(termo),
                Lead.marca.ilike(termo),
                Lead.processo_numero.ilike(termo),
            )
        )
    if status_lead:
        filtros.append(Lead.status == status_lead)

    total = (
        await session.execute(select(func.count()).select_from(Lead).where(*filtros))
    ).scalar_one()
    consulta = (
        select(Lead)
        .where(*filtros)
        .order_by(Lead.criado_em.desc())
        .limit(limite)
        .offset(deslocamento)
    )
    itens = (await session.execute(consulta)).scalars().all()
    contagens = (
        await session.execute(select(Lead.status, func.count()).group_by(Lead.status))
    ).all()
    por_status = {status.value: quantidade for status, quantidade in contagens}
    return LeadListResponse(total=total, itens=itens, por_status=por_status)


@router.patch("/v1/admin/leads/{lead_id}", response_model=LeadResponse)
async def atualizar_status_lead(
    lead_id: int,
    dados: LeadStatusUpdate,
    session: SessionDep,
    _: AdminDep,
) -> Lead:
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    lead.status = dados.status
    await session.commit()
    await session.refresh(lead)
    return lead


@router.delete("/v1/admin/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_lead(lead_id: int, session: SessionDep, _: AdminDep) -> Response:
    resultado = await session.execute(delete(Lead).where(Lead.id == lead_id))
    if not resultado.rowcount:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/v1/admin/leads.csv")
async def exportar_leads(session: SessionDep, _: AdminDep) -> StreamingResponse:
    leads = (await session.execute(select(Lead).order_by(Lead.criado_em.desc()))).scalars().all()
    arquivo = io.StringIO()
    escritor = csv.writer(arquivo, delimiter=";")
    escritor.writerow(
        (
            "id",
            "nome",
            "email",
            "telefone",
            "marca",
            "processo",
            "origem",
            "tipo",
            "status",
            "criado_em",
        )
    )
    for lead in leads:
        escritor.writerow(
            (
                lead.id,
                lead.nome,
                lead.email,
                lead.telefone,
                lead.marca,
                lead.processo_numero or "",
                lead.origem,
                lead.tipo_interesse.value if lead.tipo_interesse else "",
                lead.status.value,
                lead.criado_em.isoformat(),
            )
        )
    conteudo = "\ufeff" + arquivo.getvalue()
    return StreamingResponse(
        iter((conteudo,)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leads-inpi.csv"'},
    )

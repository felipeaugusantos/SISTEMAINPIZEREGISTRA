import csv
import io
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import AcaoAdminDep, UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import (
    AvaliacaoRiscoMarca,
    EventoAuditoria,
    Lead,
    PesquisaMarca,
    StatusLead,
    UsuarioOperacoes,
    VersaoRelatorioMarca,
)
from app.normalization import normalizar_numero_processo
from app.ratelimit import RateLimiter
from app.relatorios import gerar_pdf_relatorio
from app.schemas import (
    LeadCreate,
    LeadDetalheResponse,
    LeadListResponse,
    LeadResponse,
    LeadStatusUpdate,
    PesquisaLeadResumo,
    RelatorioMarcaResponse,
)
from app.tenancy import OrganizacaoPublicaDep

router = APIRouter(tags=["leads"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
limitar_leads = RateLimiter(limite=10, janela_segundos=60)
# Nomes preservados apenas para limpeza de estado nos testes antigos; não autenticam requisições.
limitar_admin = RateLimiter(limite=10, janela_segundos=60)
limitar_acoes_admin = RateLimiter(limite=30, janela_segundos=60)
LeadsViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
LeadsManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]
LeadsDeleteDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.delete"))]
LeadsExportDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.export"))]
BuscaLead = Annotated[str | None, Query(max_length=100)]
StatusLeadFiltro = Annotated[StatusLead | None, Query(alias="status")]
LimiteLead = Annotated[int, Query(ge=1, le=200)]
DeslocamentoLead = Annotated[int, Query(ge=0)]
DataLead = Annotated[datetime | None, Query()]
OrigemLead = Annotated[str | None, Query(max_length=30)]
ResponsavelLead = Annotated[int | None, Query(ge=1)]


def _escapar_busca(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _mascarar_email(email: str) -> str:
    local, _, dominio = email.partition("@")
    if not dominio:
        return "***"
    return f"{local[:1]}***@{dominio}"


def _mascarar_telefone(telefone: str) -> str:
    digitos = "".join(caractere for caractere in telefone if caractere.isdigit())
    return f"***{digitos[-4:]}" if digitos else "***"


def _valor_csv(valor: object) -> str:
    texto = "" if valor is None else str(valor)
    if texto.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + texto
    return texto


def _auditar(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    request: Request,
    acao: str,
    recurso: str,
    detalhes: dict,
    status_http: int = 200,
) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            ator=usuario.ator,
            acao=acao[:20],
            recurso=recurso[:180],
            sucesso=True,
            status_http=status_http,
            ip_hash=hash_ip(request.client.host if request.client else None),
            detalhes=detalhes,
        )
    )


def _filtros_lead(
    usuario: UsuarioAutenticado,
    busca: str | None,
    status_lead: StatusLead | None,
    origem: str | None,
    responsavel_id: int | None,
    data_inicio: datetime | None,
    data_fim: datetime | None,
    marketing: bool | None,
    arquivados: bool,
) -> list:
    filtros = [Lead.organizacao_id == usuario.organizacao_id]
    filtros.append(Lead.arquivado_em.is_not(None) if arquivados else Lead.arquivado_em.is_(None))
    if busca and busca.strip():
        termo = f"%{_escapar_busca(busca.strip())}%"
        filtros.append(
            or_(
                Lead.nome.ilike(termo, escape="\\"),
                Lead.email.ilike(termo, escape="\\"),
                Lead.telefone.ilike(termo, escape="\\"),
                Lead.empresa.ilike(termo, escape="\\"),
                Lead.marca.ilike(termo, escape="\\"),
                Lead.atividade.ilike(termo, escape="\\"),
                Lead.processo_numero.ilike(termo, escape="\\"),
                exists(
                    select(PesquisaMarca.id).where(
                        PesquisaMarca.lead_id == Lead.id,
                        PesquisaMarca.marca.ilike(termo, escape="\\"),
                    )
                ),
            )
        )
    if status_lead:
        filtros.append(Lead.status == status_lead)
    if origem:
        filtros.append(Lead.origem == origem)
    if responsavel_id:
        filtros.append(Lead.responsavel_id == responsavel_id)
    if data_inicio:
        filtros.append(Lead.criado_em >= data_inicio)
    if data_fim:
        filtros.append(Lead.criado_em <= data_fim)
    if marketing is not None:
        filtros.append(Lead.aceite_marketing.is_(marketing))
    return filtros


def _resumo_pesquisa(
    pesquisa: PesquisaMarca,
    risco_nivel: str | None,
    risco_pontuacao: int | None,
    relatorio_disponivel: bool,
) -> PesquisaLeadResumo:
    return PesquisaLeadResumo(
        id=pesquisa.id,
        marca=pesquisa.marca,
        atividade=pesquisa.atividade,
        classe_nice=pesquisa.classe_nice,
        criado_em=pesquisa.criado_em,
        risco_nivel=risco_nivel,
        risco_pontuacao=risco_pontuacao,
        relatorio_disponivel=relatorio_disponivel,
        relatorio_completo_gerado=pesquisa.relatorio_completo_gerado_em is not None,
        relatorio_completo_gerado_em=pesquisa.relatorio_completo_gerado_em,
        relatorio_completo_gerado_por=pesquisa.relatorio_completo_gerado_por,
        relatorio_url=f"/relatorios/{pesquisa.id}",
        pdf_url=(
            f"/v1/pesquisas-marca/{pesquisa.id}/relatorio.pdf" if relatorio_disponivel else None
        ),
    )


@router.post("/v1/admin/pesquisas/{pesquisa_id}/relatorio-completo.pdf")
async def gerar_relatorio_completo_admin(
    pesquisa_id: str,
    session: SessionDep,
    usuario: LeadsManageDep,
    _limite: AcaoAdminDep,
    request: Request,
) -> Response:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")

    versao = (
        await session.execute(
            select(VersaoRelatorioMarca)
            .where(VersaoRelatorioMarca.pesquisa_id == pesquisa.id)
            .order_by(VersaoRelatorioMarca.numero_versao.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if versao is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Abra a análise para preparar os dados antes de gerar o relatório completo.",
        )

    relatorio = RelatorioMarcaResponse.model_validate(versao.payload)
    pdf = gerar_pdf_relatorio(relatorio)
    primeira_geracao = pesquisa.relatorio_completo_gerado_em is None
    if primeira_geracao:
        pesquisa.relatorio_completo_gerado_em = datetime.now(UTC)
        pesquisa.relatorio_completo_gerado_por = usuario.ator
    _auditar(
        session,
        usuario,
        request,
        "gerar_relatorio",
        f"pesquisa:{pesquisa.id}",
        {
            "relatorio": "completo",
            "primeira_geracao": primeira_geracao,
            "versao": versao.numero_versao,
        },
    )
    await session.commit()
    nome_arquivo = normalizar_numero_processo(relatorio.marca) or "relatorio"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="relatorio-completo-{nome_arquivo}.pdf"'
            ),
            "X-Relatorio-Completo-Primeira-Geracao": str(primeira_geracao).lower(),
        },
    )


def _lead_response(
    lead: Lead,
    usuario: UsuarioAutenticado,
    pesquisas: list[PesquisaLeadResumo] | None = None,
) -> LeadResponse:
    dados = LeadResponse.model_validate(lead)
    if not usuario.pode("leads.pii.view"):
        dados.email = _mascarar_email(dados.email)
        dados.telefone = _mascarar_telefone(dados.telefone)
    dados.responsavel_nome = getattr(getattr(lead, "responsavel", None), "nome", None)
    dados.total_pesquisas = len(pesquisas or [])
    dados.ultima_pesquisa = pesquisas[0] if pesquisas else None
    return dados


@router.post(
    "/v1/leads",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limitar_leads)],
)
async def criar_lead(
    dados: LeadCreate,
    session: SessionDep,
    organizacao: OrganizacaoPublicaDep,
) -> Lead:
    if dados.website:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Envio inválido")

    consulta_existente = (
        select(Lead)
        .where(
            Lead.organizacao_id == organizacao.id,
            Lead.arquivado_em.is_(None),
            or_(
                func.lower(Lead.email) == dados.email.lower(),
                Lead.telefone == dados.telefone,
            ),
        )
        .order_by(
            case((func.lower(Lead.email) == dados.email.lower(), 0), else_=1),
            Lead.atualizado_em.desc(),
        )
        .limit(1)
    )
    existente = (await session.execute(consulta_existente)).scalar_one_or_none()
    if existente is not None:
        existente.nome = dados.nome
        existente.email = dados.email.lower()
        existente.telefone = dados.telefone
        existente.marca = dados.marca or existente.marca
        existente.processo_numero = dados.processo_numero or existente.processo_numero
        existente.origem = dados.origem
        existente.tipo_interesse = dados.tipo_interesse or existente.tipo_interesse
        await session.commit()
        await session.refresh(existente)
        return existente

    lead = Lead(
        organizacao_id=organizacao.id,
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
    usuario: LeadsViewDep,
    busca: BuscaLead = None,
    status_lead: StatusLeadFiltro = None,
    limite: LimiteLead = 50,
    deslocamento: DeslocamentoLead = 0,
    origem: OrigemLead = None,
    responsavel_id: ResponsavelLead = None,
    data_inicio: DataLead = None,
    data_fim: DataLead = None,
    marketing: bool | None = None,
    arquivados: bool = False,
) -> LeadListResponse:
    filtros = _filtros_lead(
        usuario,
        busca,
        status_lead,
        origem,
        responsavel_id,
        data_inicio,
        data_fim,
        marketing,
        arquivados,
    )

    total = (
        await session.execute(select(func.count()).select_from(Lead).where(*filtros))
    ).scalar_one()
    filtros_globais = [
        Lead.organizacao_id == usuario.organizacao_id,
        Lead.arquivado_em.is_(None),
    ]
    total_global = (
        await session.execute(select(func.count()).select_from(Lead).where(*filtros_globais))
    ).scalar_one()
    consulta = (
        select(Lead)
        .options(selectinload(Lead.responsavel))
        .where(*filtros)
        .order_by(Lead.criado_em.desc())
        .limit(limite)
        .offset(deslocamento)
    )
    itens = (await session.execute(consulta)).scalars().all()
    contagens = (
        await session.execute(
            select(Lead.status, func.count()).where(*filtros_globais).group_by(Lead.status)
        )
    ).all()
    pesquisas_total = (
        await session.execute(
            select(func.count())
            .select_from(PesquisaMarca)
            .where(PesquisaMarca.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one()
    pesquisas_por_lead: dict[int, list[PesquisaLeadResumo]] = {}
    ids = [item.id for item in itens]
    if ids:
        relatorio_existe = exists(
            select(VersaoRelatorioMarca.id).where(
                VersaoRelatorioMarca.pesquisa_id == PesquisaMarca.id
            )
        )
        linhas = (
            await session.execute(
                select(
                    PesquisaMarca,
                    AvaliacaoRiscoMarca.nivel,
                    AvaliacaoRiscoMarca.pontuacao,
                    relatorio_existe.label("relatorio_disponivel"),
                )
                .outerjoin(
                    AvaliacaoRiscoMarca,
                    AvaliacaoRiscoMarca.pesquisa_id == PesquisaMarca.id,
                )
                .where(PesquisaMarca.lead_id.in_(ids))
                .order_by(PesquisaMarca.criado_em.desc())
            )
        ).all()
        for pesquisa, nivel, pontuacao, disponivel in linhas:
            pesquisas_por_lead.setdefault(pesquisa.lead_id, []).append(
                _resumo_pesquisa(pesquisa, nivel, pontuacao, bool(disponivel))
            )
    por_status = {status.value: quantidade for status, quantidade in contagens}
    return LeadListResponse(
        total=total,
        total_global=total_global,
        pesquisas_total=pesquisas_total,
        deslocamento=deslocamento,
        limite=limite,
        tem_mais=deslocamento + len(itens) < total,
        itens=[
            _lead_response(item, usuario, pesquisas_por_lead.get(item.id, [])) for item in itens
        ],
        por_status=por_status,
        acoes={
            "gerenciar": usuario.pode("leads.manage"),
            "arquivar": usuario.pode("leads.delete"),
            "exportar": usuario.pode("leads.export") and usuario.pode("leads.pii.view"),
            "ver_pii": usuario.pode("leads.pii.view"),
        },
    )


@router.patch("/v1/admin/leads/{lead_id}", response_model=LeadResponse)
async def atualizar_status_lead(
    lead_id: int,
    dados: LeadStatusUpdate,
    session: SessionDep,
    usuario: LeadsManageDep,
    _limite: AcaoAdminDep,
    request: Request,
) -> LeadResponse:
    lead = (
        await session.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.organizacao_id == usuario.organizacao_id,
                Lead.arquivado_em.is_(None),
            )
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    alteracoes: dict[str, object] = {}
    if dados.status is not None and dados.status != lead.status:
        alteracoes["status"] = {"de": lead.status.value, "para": dados.status.value}
        lead.status = dados.status
    if "responsavel_id" in dados.model_fields_set:
        if dados.responsavel_id is not None:
            responsavel = (
                await session.execute(
                    select(UsuarioOperacoes).where(
                        UsuarioOperacoes.id == dados.responsavel_id,
                        UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
                        UsuarioOperacoes.ativo.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if responsavel is None:
                raise HTTPException(status_code=422, detail="Responsavel invalido")
        alteracoes["responsavel_id"] = {"de": lead.responsavel_id, "para": dados.responsavel_id}
        lead.responsavel_id = dados.responsavel_id
    if "notas" in dados.model_fields_set:
        alteracoes["notas_atualizadas"] = True
        lead.notas = dados.notas
    if "proxima_acao_em" in dados.model_fields_set:
        alteracoes["proxima_acao_em"] = (
            dados.proxima_acao_em.isoformat() if dados.proxima_acao_em else None
        )
        lead.proxima_acao_em = dados.proxima_acao_em
    if dados.tags is not None:
        alteracoes["tags"] = dados.tags
        lead.tags = dados.tags
    if dados.registrar_contato:
        lead.ultimo_contato_em = datetime.now(UTC)
        alteracoes["contato_registrado"] = True
    _auditar(session, usuario, request, "alterar", f"lead:{lead.id}", alteracoes)
    await session.commit()
    await session.refresh(lead)
    return _lead_response(lead, usuario)


@router.get("/v1/admin/leads-responsaveis")
async def listar_responsaveis(
    session: SessionDep,
    usuario: LeadsViewDep,
) -> dict:
    usuarios = (
        await session.execute(
            select(UsuarioOperacoes.id, UsuarioOperacoes.nome)
            .where(
                UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
                UsuarioOperacoes.ativo.is_(True),
            )
            .order_by(UsuarioOperacoes.nome)
        )
    ).all()
    return {"itens": [{"id": item.id, "nome": item.nome} for item in usuarios]}


@router.get("/v1/admin/leads/{lead_id}", response_model=LeadDetalheResponse)
async def detalhar_lead(
    lead_id: int,
    session: SessionDep,
    usuario: LeadsViewDep,
) -> LeadDetalheResponse:
    lead = (
        await session.execute(
            select(Lead)
            .options(selectinload(Lead.responsavel))
            .where(
                Lead.id == lead_id,
                Lead.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead nao encontrado")
    relatorio_existe = exists(
        select(VersaoRelatorioMarca.id).where(VersaoRelatorioMarca.pesquisa_id == PesquisaMarca.id)
    )
    linhas = (
        await session.execute(
            select(
                PesquisaMarca,
                AvaliacaoRiscoMarca.nivel,
                AvaliacaoRiscoMarca.pontuacao,
                relatorio_existe.label("relatorio_disponivel"),
            )
            .outerjoin(AvaliacaoRiscoMarca, AvaliacaoRiscoMarca.pesquisa_id == PesquisaMarca.id)
            .where(
                PesquisaMarca.lead_id == lead.id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
            .order_by(PesquisaMarca.criado_em.desc())
        )
    ).all()
    pesquisas = [
        _resumo_pesquisa(pesquisa, nivel, pontuacao, bool(disponivel))
        for pesquisa, nivel, pontuacao, disponivel in linhas
    ]
    base = _lead_response(lead, usuario, pesquisas)
    return LeadDetalheResponse(**base.model_dump(), pesquisas=pesquisas)


@router.delete("/v1/admin/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_lead(
    lead_id: int,
    session: SessionDep,
    usuario: LeadsDeleteDep,
    _limite: AcaoAdminDep,
    request: Request,
) -> Response:
    lead = (
        await session.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.organizacao_id == usuario.organizacao_id,
                Lead.arquivado_em.is_(None),
            )
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    lead.arquivado_em = datetime.now(UTC)
    _auditar(
        session,
        usuario,
        request,
        "arquivar",
        f"lead:{lead.id}",
        {"pesquisas_preservadas": True},
        status_http=204,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/v1/admin/leads/{lead_id}/restaurar", response_model=LeadResponse)
async def restaurar_lead(
    lead_id: int,
    session: SessionDep,
    usuario: LeadsDeleteDep,
    _limite: AcaoAdminDep,
    request: Request,
) -> LeadResponse:
    lead = (
        await session.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.organizacao_id == usuario.organizacao_id,
                Lead.arquivado_em.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead arquivado nao encontrado")
    lead.arquivado_em = None
    _auditar(session, usuario, request, "restaurar", f"lead:{lead.id}", {})
    await session.commit()
    await session.refresh(lead)
    return _lead_response(lead, usuario)


@router.get("/v1/admin/leads.csv")
async def exportar_leads(
    session: SessionDep,
    usuario: LeadsExportDep,
    request: Request,
    busca: BuscaLead = None,
    status_lead: StatusLeadFiltro = None,
    origem: OrigemLead = None,
    responsavel_id: ResponsavelLead = None,
    data_inicio: DataLead = None,
    data_fim: DataLead = None,
    marketing: bool | None = None,
) -> StreamingResponse:
    if not usuario.pode("leads.pii.view"):
        raise HTTPException(status_code=403, detail="Sem permissao para exportar dados de contato")
    filtros = _filtros_lead(
        usuario,
        busca,
        status_lead,
        origem,
        responsavel_id,
        data_inicio,
        data_fim,
        marketing,
        False,
    )
    leads = (
        (
            await session.execute(
                select(Lead).where(*filtros).order_by(Lead.criado_em.desc()).limit(5000)
            )
        )
        .scalars()
        .all()
    )
    arquivo = io.StringIO()
    escritor = csv.writer(arquivo, delimiter=";")
    escritor.writerow(
        (
            "id",
            "nome",
            "email",
            "telefone",
            "empresa",
            "marca",
            "atividade",
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
                _valor_csv(lead.id),
                _valor_csv(lead.nome),
                _valor_csv(lead.email),
                _valor_csv(lead.telefone),
                _valor_csv(lead.empresa),
                _valor_csv(lead.marca),
                _valor_csv(lead.atividade),
                _valor_csv(lead.processo_numero),
                _valor_csv(lead.origem),
                _valor_csv(lead.tipo_interesse.value if lead.tipo_interesse else ""),
                _valor_csv(lead.status.value),
                _valor_csv(lead.criado_em.isoformat()),
            )
        )
    _auditar(
        session,
        usuario,
        request,
        "exportar",
        "leads:csv",
        {
            "quantidade": len(leads),
            "filtros": {
                "busca": busca,
                "status": status_lead.value if status_lead else None,
                "origem": origem,
            },
        },
    )
    await session.commit()
    conteudo = "\ufeff" + arquivo.getvalue()
    return StreamingResponse(
        iter((conteudo,)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leads-inpi.csv"'},
    )

import csv
import hashlib
import html
import io
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import AcaoAdminDep, UsuarioAutenticado, exigir_permissao, hash_ip
from app.clicksign import configuracao as configuracao_clicksign
from app.clicksign import criar_envelope
from app.crm import (
    aplicar_politica_oportunidade,
    aplicar_regras_automacao,
    avancar_fase_lead,
    registrar_evento_operacional,
    sincronizar_fase_por_status,
)
from app.database import get_session
from app.emailing import enviar_proposta_email
from app.models import (
    MOTIVOS_PERDA,
    ORDEM_FASE_LEAD,
    TIPOS_DOCUMENTO_LEAD,
    AssinaturaPropostaComercial,
    AvaliacaoRiscoMarca,
    Cadencia,
    CanalContato,
    ChecklistFaseLead,
    Contato,
    ContatoLead,
    DocumentoLead,
    EmpresaCRM,
    EventoAuditoria,
    EventoDominio,
    FaseLead,
    GuiaInpi,
    HistoricoFaseLead,
    Lead,
    LembreteCRM,
    MensagemClientePortal,
    Organizacao,
    PesquisaMarca,
    PropostaComercial,
    RetribuicaoInpi,
    SolicitacaoExclusaoPesquisa,
    StatusLead,
    UsuarioOperacoes,
    VersaoDocumentoLead,
    VersaoRelatorioMarca,
)
from app.normalization import normalizar_numero_processo
from app.proxy import cliente_ip
from app.ratelimit import RateLimiter
from app.relatorios import gerar_pdf_proposta, gerar_pdf_relatorio
from app.schemas import (
    LeadCreate,
    LeadDetalheResponse,
    LeadListResponse,
    LeadResponse,
    LeadStatusUpdate,
    PesquisaLeadResumo,
    RelatorioMarcaResponse,
)
from app.settings import get_settings
from app.tenancy import OrganizacaoPublicaDep, aplicar_contexto_tenant
from app.trademarks.analysis_workflow import EstadoAnalise, revisao_obrigatoria_pendente
from app.trademarks.consolidated import analise_para_exibicao

router = APIRouter(tags=["leads"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
limitar_leads = RateLimiter(limite=10, janela_segundos=60, escopo="leads-publicos")
# Nomes preservados apenas para limpeza de estado nos testes antigos; não autenticam requisições.
limitar_admin = RateLimiter(limite=10, janela_segundos=60, escopo="admin-legado")
limitar_acoes_admin = RateLimiter(limite=30, janela_segundos=60, escopo="admin-acoes")
LeadsViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
LeadsManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]
LeadsDeleteDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.delete"))]
LeadsExportDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.export"))]
BuscaLead = Annotated[str | None, Query(max_length=100)]
StatusLeadFiltro = Annotated[StatusLead | None, Query(alias="status")]
LimiteLead = Annotated[int, Query(ge=1, le=200)]
DeslocamentoLead = Annotated[int, Query(ge=0)]

KANBAN_ETAPAS = {
    "primeiro_contato": {"label": "Primeiro contato", "fase": "contato_inicial"},
    "aguardando_contato_nosso": {"label": "Aguardando contato nosso", "fase": "contato_inicial"},
    "aguardando_retorno_cliente": {
        "label": "Aguardando retorno do cliente",
        "fase": "relatorio_enviado",
    },
    "proposta_enviada": {"label": "Proposta enviada", "fase": "proposta_enviada"},
    "proposta_aceita": {"label": "Proposta aceita / contrato enviado", "fase": "proposta_aceita"},
    "pagamento_realizado": {"label": "Pagamento realizado", "fase": "pagamento_realizado"},
    "protocolo_inpi": {"label": "Protocolo no INPI gerado", "fase": "protocolo_inpi"},
    "processo_inpi": {"label": "Processo no INPI", "fase": "processo_inpi"},
}
KANBAN_ORDEM = tuple(KANBAN_ETAPAS)
DataLead = Annotated[datetime | None, Query()]
OrigemLead = Annotated[str | None, Query(max_length=30)]
ResponsavelLead = Annotated[int | None, Query(ge=1)]
PrioridadeLead = Annotated[
    Literal["atrasadas", "sem_responsavel", "sem_proxima_acao"] | None,
    Query(),
]


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


def _mascarar_documento(documento: str | None) -> str | None:
    if not documento:
        return None
    digitos = "".join(caractere for caractere in documento if caractere.isdigit())
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
            actor_id=usuario.id,
            ator=usuario.ator,
            acao=acao[:20],
            recurso=recurso[:180],
            sucesso=status_http < 400,
            status_http=status_http,
            ip_hash=hash_ip(cliente_ip(request)),
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
    prioridade: str | None = None,
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
                Lead.documento.ilike(termo, escape="\\"),
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
    if prioridade == "atrasadas":
        filtros.extend(
            (
                Lead.proxima_acao_em.is_not(None),
                Lead.proxima_acao_em < datetime.now(UTC),
                Lead.status.not_in((StatusLead.CONVERTIDO, StatusLead.DESCARTADO)),
            )
        )
    elif prioridade == "sem_responsavel":
        filtros.extend(
            (
                Lead.responsavel_id.is_(None),
                Lead.status.not_in((StatusLead.CONVERTIDO, StatusLead.DESCARTADO)),
            )
        )
    elif prioridade == "sem_proxima_acao":
        filtros.extend(
            (
                Lead.proxima_acao_em.is_(None),
                Lead.status.not_in((StatusLead.CONVERTIDO, StatusLead.DESCARTADO)),
            )
        )
    return filtros


def _resumo_pesquisa(
    pesquisa: PesquisaMarca,
    risco_nivel: str | None,
    risco_pontuacao: int | None,
    relatorio_disponivel: bool,
    exclusao_status: str | None = None,
) -> PesquisaLeadResumo:
    estado_analise = pesquisa.analysis_state or EstadoAnalise.DRAFT.value
    relatorio_validado_gerado = bool(
        estado_analise == EstadoAnalise.VALIDATED.value
        and pesquisa.validated_at is not None
        and pesquisa.relatorio_completo_gerado_em is not None
    )
    return PesquisaLeadResumo(
        id=pesquisa.id,
        marca=pesquisa.marca,
        atividade=pesquisa.atividade,
        classe_nice=pesquisa.classe_nice,
        duplicada=bool(pesquisa.duplicada),
        pesquisa_original_id=pesquisa.pesquisa_original_id,
        criado_em=pesquisa.criado_em,
        risco_nivel=risco_nivel,
        risco_pontuacao=risco_pontuacao,
        relatorio_disponivel=relatorio_disponivel,
        relatorio_completo_gerado=relatorio_validado_gerado,
        relatorio_completo_gerado_em=pesquisa.relatorio_completo_gerado_em,
        relatorio_completo_gerado_por=pesquisa.relatorio_completo_gerado_por,
        analysis_state=estado_analise,
        review_required=revisao_obrigatoria_pendente(estado_analise),
        validated_by=pesquisa.validated_by,
        validated_at=pesquisa.validated_at,
        relatorio_url=f"/relatorios/{pesquisa.id}",
        pdf_url=(f"/v1/pesquisas-marca/{pesquisa.id}/relatorio.pdf" if relatorio_disponivel else None),
        exclusao_status=exclusao_status,
    )


@router.post("/v1/admin/pesquisas/{pesquisa_id}/relatorio-completo.pdf")
async def gerar_relatorio_completo_admin(
    pesquisa_id: str,
    session: SessionDep,
    usuario: LeadsManageDep,
    _limite: AcaoAdminDep,
    request: Request,
    versao_esperada: int | None = Query(default=None, ge=1),
) -> Response:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca)
            .where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
            .with_for_update()
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
            .with_for_update()
        )
    ).scalar_one_or_none()
    if versao is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Abra a análise para preparar os dados antes de gerar o relatório completo.",
        )
    if versao_esperada is not None and versao.numero_versao != versao_esperada:
        # A tela carregou uma versão e, antes do clique, outra ação (ex.: parecer
        # humano, dados complementares) já criou uma versão mais nova -- gerar o PDF
        # da versão antiga sem avisar seria mostrar um documento que já não reflete
        # o que está na tela. Achado da auditoria Fase 1 (item 6).
        _auditar(
            session,
            usuario,
            request,
            "bloquear_relatorio",
            f"pesquisa:{pesquisa.id}",
            {
                "motivo": "versao_desatualizada",
                "versao_esperada": versao_esperada,
                "versao_atual": versao.numero_versao,
            },
            status_http=status.HTTP_409_CONFLICT,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A análise mudou desde que a tela foi carregada (versão atual: {versao.numero_versao}). "
                "Recarregue a página antes de gerar o relatório."
            ),
        )
    permitir_relatorio_preliminar = True
    if (not permitir_relatorio_preliminar) and (
        pesquisa.analysis_state != EstadoAnalise.VALIDATED.value
        or pesquisa.validated_at is None
        or pesquisa.validated_by is None
        or versao.validated_at is None
        or versao.validated_by is None
    ):
        _auditar(
            session,
            usuario,
            request,
            "bloquear_relatorio",
            f"pesquisa:{pesquisa.id}",
            {
                "motivo": "revisao_obrigatoria_pendente",
                "analysis_state": pesquisa.analysis_state,
                "versao": versao.numero_versao,
            },
            status_http=status.HTTP_409_CONFLICT,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "O relatório completo só pode ser emitido após a revisão obrigatória e a "
                "validação formal da versão atual."
            ),
        )

    relatorio = RelatorioMarcaResponse.model_validate(versao.payload)
    relatorio.analise_consolidada = analise_para_exibicao(
        versao.payload,
        versao=versao.numero_versao,
        validado_por=versao.validated_by if pesquisa.analysis_state == EstadoAnalise.VALIDATED.value else None,
        validado_em=versao.validated_at if pesquisa.analysis_state == EstadoAnalise.VALIDATED.value else None,
    )
    validado = relatorio.analise_consolidada["revisao"]["validada"]
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
            "relatorio": "validado" if validado else "preliminar",
            "primeira_geracao": primeira_geracao,
            "versao": versao.numero_versao,
            "analysis_state": pesquisa.analysis_state,
            "validated_by": pesquisa.validated_by,
            "validated_at": pesquisa.validated_at.isoformat() if pesquisa.validated_at else None,
        },
    )
    await session.commit()
    nome_arquivo = normalizar_numero_processo(relatorio.marca) or "relatorio"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "X-Relatorio-Status": "validado"
            if validado
            else "preliminar",
            "Content-Disposition": (f'attachment; filename="relatorio-completo-{nome_arquivo}.pdf"'),
            "X-Relatorio-Completo-Primeira-Geracao": str(primeira_geracao).lower(),
            "X-Analysis-State": pesquisa.analysis_state or "PENDING_REVIEW",
            "X-Validated-By": pesquisa.validated_by or "",
        },
    )


def _lead_response(
    lead: Lead,
    usuario: UsuarioAutenticado,
    pesquisas: list[PesquisaLeadResumo] | None = None,
    mensagens_portal_pendentes: int = 0,
) -> LeadResponse:
    pesquisas = pesquisas or []
    dados = LeadResponse.model_validate(lead)
    if not usuario.pode("leads.pii.view"):
        dados.email = _mascarar_email(dados.email)
        dados.telefone = _mascarar_telefone(dados.telefone)
        dados.documento = _mascarar_documento(dados.documento)
    dados.responsavel_nome = getattr(getattr(lead, "responsavel", None), "nome", None)
    dados.total_pesquisas = len(pesquisas)
    dados.ultima_pesquisa = pesquisas[0] if pesquisas else None
    dados.ultima_pesquisa_em = pesquisas[0].criado_em if pesquisas else lead.criado_em
    dados.relatorios_completos_gerados = sum(item.relatorio_completo_gerado for item in pesquisas)
    dados.mensagens_portal_pendentes = mensagens_portal_pendentes
    dados.pesquisas = pesquisas
    pesquisas_com_risco = [item for item in pesquisas if item.risco_nivel]
    if pesquisas_com_risco:
        ordem_risco = {
            "baixo": 1,
            "moderado": 2,
            "alto": 3,
            "muito_alto": 4,
            "critico": 5,
        }
        maior_risco = max(
            pesquisas_com_risco,
            key=lambda item: (
                ordem_risco.get(item.risco_nivel or "", 0),
                item.risco_pontuacao if item.risco_pontuacao is not None else -1,
            ),
        )
        dados.risco_mais_alto = maior_risco.risco_nivel
        dados.risco_mais_alto_pontuacao = maior_risco.risco_pontuacao
    return dados


# Achado da auditoria do CRM: todo lead que chega pelo formulário público
# nascia sem proxima_acao_em, sem nunca passar pela política de CRM (que só é
# aplicada em atualizar_status_lead). Aqui a falta não pode virar bloqueio --
# é a porta de entrada mais comum, travar o formulário do site é pior do que
# aplicar um prazo padrão -- então só aplica o fallback, nunca levanta 422.
DIAS_PROXIMA_ACAO_CAPTACAO_PADRAO = 2


async def _garantir_proxima_acao_padrao(session: AsyncSession, lead: Lead) -> None:
    await aplicar_politica_oportunidade(session, lead)
    if lead.proxima_acao_em is None:
        lead.proxima_acao_em = datetime.now(UTC) + timedelta(days=DIAS_PROXIMA_ACAO_CAPTACAO_PADRAO)


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
        if existente.status not in (StatusLead.CONVERTIDO, StatusLead.DESCARTADO):
            await _garantir_proxima_acao_padrao(session, existente)
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
    await _garantir_proxima_acao_padrao(session, lead)
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
    prioridade: PrioridadeLead = None,
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
        prioridade,
    )

    total = (await session.execute(select(func.count()).select_from(Lead).where(*filtros))).scalar_one()
    filtros_globais = [
        Lead.organizacao_id == usuario.organizacao_id,
        Lead.arquivado_em.is_(None),
    ]
    total_global = (await session.execute(select(func.count()).select_from(Lead).where(*filtros_globais))).scalar_one()
    consulta = (
        select(Lead)
        .options(selectinload(Lead.responsavel))
        .where(*filtros)
        .order_by(Lead.criado_em.desc(), Lead.id.desc())
        .limit(limite)
        .offset(deslocamento)
    )
    itens = (await session.execute(consulta)).scalars().all()
    contagens = (
        await session.execute(select(Lead.status, func.count()).where(*filtros_globais).group_by(Lead.status))
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
    mensagens_pendentes_por_lead: dict[int, int] = {}
    if ids:
        contagens_mensagens = await session.execute(
            select(MensagemClientePortal.lead_id, func.count())
            .where(
                MensagemClientePortal.organizacao_id == usuario.organizacao_id,
                MensagemClientePortal.lead_id.in_(ids),
                MensagemClientePortal.autor_tipo == "cliente",
                MensagemClientePortal.lida_em.is_(None),
            )
            .group_by(MensagemClientePortal.lead_id)
        )
        mensagens_pendentes_por_lead = {lead_id: total for lead_id, total in contagens_mensagens.all()}
        relatorio_existe = exists(
            select(VersaoRelatorioMarca.id).where(VersaoRelatorioMarca.pesquisa_id == PesquisaMarca.id)
        )
        exclusao_pendente = (
            select(SolicitacaoExclusaoPesquisa.status)
            .where(
                SolicitacaoExclusaoPesquisa.pesquisa_id == PesquisaMarca.id,
                SolicitacaoExclusaoPesquisa.status == "pendente",
            )
            .limit(1)
            .scalar_subquery()
        )
        linhas = (
            await session.execute(
                select(
                    PesquisaMarca,
                    AvaliacaoRiscoMarca.nivel,
                    AvaliacaoRiscoMarca.pontuacao,
                    relatorio_existe.label("relatorio_disponivel"),
                    exclusao_pendente.label("exclusao_status"),
                )
                .outerjoin(
                    AvaliacaoRiscoMarca,
                    AvaliacaoRiscoMarca.pesquisa_id == PesquisaMarca.id,
                )
                .where(PesquisaMarca.lead_id.in_(ids))
                .order_by(PesquisaMarca.criado_em.desc())
            )
        ).all()
        for pesquisa, nivel, pontuacao, disponivel, exclusao_status in linhas:
            pesquisas_por_lead.setdefault(pesquisa.lead_id, []).append(
                _resumo_pesquisa(pesquisa, nivel, pontuacao, bool(disponivel), exclusao_status)
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
            _lead_response(
                item,
                usuario,
                pesquisas_por_lead.get(item.id, []),
                mensagens_pendentes_por_lead.get(item.id, 0),
            )
            for item in itens
        ],
        por_status=por_status,
        acoes={
            "gerenciar": usuario.pode("leads.manage"),
            "arquivar": usuario.pode("leads.delete"),
            "exportar": usuario.pode("leads.export") and usuario.pode("leads.pii.view"),
            "ver_pii": usuario.pode("leads.pii.view"),
            "excluir_pesquisa": usuario.pode("leads.delete"),
        },
    )


@router.get("/v1/admin/leads-crm")
async def resumo_crm_leads(session: SessionDep, usuario: LeadsViewDep) -> dict:
    agora = datetime.now(UTC)
    em_aberto = Lead.status.not_in((StatusLead.CONVERTIDO, StatusLead.DESCARTADO))
    linha = (
        await session.execute(
            select(
                func.count().filter(Lead.responsavel_id.is_(None), em_aberto),
                func.count().filter(
                    Lead.proxima_acao_em.is_not(None),
                    Lead.proxima_acao_em < agora,
                    em_aberto,
                ),
                func.count().filter(Lead.proxima_acao_em.is_(None), em_aberto),
            ).where(
                Lead.organizacao_id == usuario.organizacao_id,
                Lead.arquivado_em.is_(None),
            )
        )
    ).one()
    return {
        "sem_responsavel": int(linha[0] or 0),
        "atrasadas": int(linha[1] or 0),
        "sem_proxima_acao": int(linha[2] or 0),
        "atualizado_em": agora,
    }


def _kanban_etapa(lead: Lead) -> str:
    if lead.fase == "contato_inicial":
        return "aguardando_contato_nosso" if lead.status == StatusLead.EM_CONTATO else "primeiro_contato"
    if lead.fase == "relatorio_enviado" and lead.status == StatusLead.SEM_RETORNO:
        return "aguardando_retorno_cliente"
    return lead.fase if lead.fase in KANBAN_ETAPAS else "primeiro_contato"


@router.get("/v1/admin/leads-kanban")
async def listar_leads_kanban(session: SessionDep, usuario: LeadsViewDep) -> dict:
    leads = (
        (
            await session.execute(
                select(Lead)
                .options(selectinload(Lead.responsavel))
                .where(Lead.organizacao_id == usuario.organizacao_id, Lead.arquivado_em.is_(None))
                .order_by(Lead.criado_em.desc(), Lead.id.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    cards = [
        {
            "id": lead.id,
            "nome": lead.nome,
            "empresa": lead.empresa,
            "marca": lead.marca,
            "etapa": _kanban_etapa(lead),
            "responsavel": getattr(lead.responsavel, "nome", None),
            "proxima_acao_em": lead.proxima_acao_em,
            "status": lead.status.value,
        }
        for lead in leads
    ]
    return {
        "etapas": [{"id": etapa, **dados} for etapa, dados in KANBAN_ETAPAS.items()],
        "cards": cards,
        "acoes": {"gerenciar": usuario.pode("leads.manage")},
    }


class KanbanEtapaInput(BaseModel):
    etapa: str = Field(min_length=3, max_length=40)


@router.post("/v1/admin/leads/{lead_id}/kanban")
async def mover_lead_kanban(
    lead_id: int,
    dados: KanbanEtapaInput,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    etapa = KANBAN_ETAPAS.get(dados.etapa)
    if etapa is None:
        raise HTTPException(status_code=422, detail="Etapa do Kanban inválida")
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if etapa["fase"] in {"protocolo_inpi", "processo_inpi"}:
        documentos = (
            (
                await session.execute(
                    select(DocumentoLead).where(
                        DocumentoLead.lead_id == lead.id,
                        DocumentoLead.organizacao_id == lead.organizacao_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        por_tipo = {documento.tipo: documento for documento in documentos}
        obrigatorios = {documento.tipo for documento in documentos if documento.obrigatorio} | {"procuracao"}
        pendencias = [
            tipo for tipo in obrigatorios if tipo not in por_tipo or por_tipo[tipo].status not in DOCUMENTOS_VALIDOS
        ]
        if pendencias:
            raise HTTPException(
                status_code=422,
                detail=f"Etapa bloqueada. Documentos obrigatórios pendentes: {', '.join(sorted(pendencias))}.",
            )
    await avancar_fase_lead(session, lead, etapa["fase"], por=usuario.nome or "operador", forcar=True)
    if dados.etapa == "primeiro_contato":
        lead.status = StatusLead.NOVO
    elif dados.etapa == "aguardando_contato_nosso":
        lead.status = StatusLead.EM_CONTATO
    elif dados.etapa == "aguardando_retorno_cliente":
        lead.status = StatusLead.SEM_RETORNO
    # Mover o card também é "mexer no CRM": oportunidade aberta exige responsável
    # e próxima ação, mesma regra aplicada em atualizar_status_lead.
    if lead.status not in (StatusLead.CONVERTIDO, StatusLead.DESCARTADO):
        faltando = await aplicar_politica_oportunidade(session, lead, usuario.id)
        if faltando:
            raise HTTPException(
                status_code=422,
                detail=f"Oportunidade aberta exige {' e '.join(faltando)}.",
            )
    _auditar(
        session,
        usuario,
        request,
        "mover_kanban",
        f"lead:{lead.id}",
        {"etapa": dados.etapa, "fase": lead.fase},
    )
    await session.commit()
    return {"id": lead.id, "etapa": _kanban_etapa(lead), "fase": lead.fase}


@router.get("/v1/admin/leads-dashboard")
async def dashboard_funil_produtividade(session: SessionDep, usuario: LeadsViewDep) -> dict:
    org = usuario.organizacao_id
    base = (Lead.organizacao_id == org, Lead.arquivado_em.is_(None))
    agora = datetime.now(UTC)
    aberta = Lead.status.not_in((StatusLead.CONVERTIDO, StatusLead.DESCARTADO))

    por_fase = dict((await session.execute(select(Lead.fase, func.count()).where(*base).group_by(Lead.fase))).all())
    funil = [{"fase": f, "label": FASE_LABELS.get(f, f), "total": int(por_fase.get(f, 0))} for f in ORDEM_FASE_LEAD]

    por_resultado = dict(
        (await session.execute(select(Lead.resultado, func.count()).where(*base).group_by(Lead.resultado))).all()
    )
    ganho = int(por_resultado.get("ganho", 0))
    perdido = int(por_resultado.get("perdido", 0))
    aberto = int(sum(v for k, v in por_resultado.items() if k not in ("ganho", "perdido")))
    fechados = ganho + perdido

    motivos = (
        await session.execute(
            select(Lead.motivo_perda, func.count())
            .where(*base, Lead.resultado == "perdido")
            .group_by(Lead.motivo_perda)
        )
    ).all()
    perdas_por_motivo = [
        {
            "motivo": m or "nao_informado",
            "label": MOTIVOS_PERDA.get(m, "Não informado") if m else "Não informado",
            "total": int(c),
        }
        for m, c in sorted(motivos, key=lambda r: r[1], reverse=True)
    ]

    prod = (
        await session.execute(
            select(
                Lead.responsavel_id,
                func.count().filter(aberta),
                func.count().filter(Lead.proxima_acao_em.is_not(None), Lead.proxima_acao_em < agora, aberta),
                func.count().filter(Lead.resultado == "ganho"),
                func.count().filter(Lead.resultado == "perdido"),
            )
            .where(*base)
            .group_by(Lead.responsavel_id)
        )
    ).all()
    ids = [r[0] for r in prod if r[0] is not None]
    nomes = (
        dict(
            (
                await session.execute(
                    select(UsuarioOperacoes.id, UsuarioOperacoes.nome).where(UsuarioOperacoes.id.in_(ids))
                )
            ).all()
        )
        if ids
        else {}
    )
    produtividade = sorted(
        (
            {
                "responsavel_id": rid,
                "nome": nomes.get(rid, "Sem responsável") if rid else "Sem responsável",
                "abertas": int(ab),
                "atrasadas": int(atr),
                "ganhos": int(gan),
                "perdidos": int(per),
            }
            for rid, ab, atr, gan, per in prod
        ),
        key=lambda x: (-x["abertas"], -x["ganhos"]),
    )

    por_origem = dict(
        (await session.execute(select(Lead.origem, func.count()).where(*base).group_by(Lead.origem))).all()
    )
    leads = list((await session.execute(select(Lead).where(*base))).scalars())
    atrasados = sum(
        1
        for item in leads
        if item.status not in (StatusLead.CONVERTIDO, StatusLead.DESCARTADO)
        and item.proxima_acao_em is not None
        and item.proxima_acao_em < agora
    )
    tempo_ate_proposta = [
        (item.atualizado_em - item.criado_em).total_seconds() / 86400
        for item in leads
        if item.fase
        in {
            "proposta_enviada",
            "proposta_aceita",
            "pagamento_realizado",
            "protocolo_inpi",
            "processo_inpi",
        }
        and item.atualizado_em
        and item.criado_em
    ]
    propostas = list(
        (await session.execute(select(PropostaComercial).where(PropostaComercial.organizacao_id == org))).scalars()
    )
    aceites = [
        (item.aceito_em - item.enviado_em).total_seconds() / 86400
        for item in propostas
        if item.aceito_em and item.enviado_em
    ]
    pagas = sum(1 for item in propostas if item.pagamento_status == "confirmado")
    protocoladas = sum(1 for item in propostas if item.protocolo_em)

    return {
        "funil": funil,
        "resultado": {"aberto": aberto, "ganho": ganho, "perdido": perdido},
        "taxa_conversao": round(ganho / fechados, 4) if fechados else 0,
        "perdas_por_motivo": perdas_por_motivo,
        "produtividade": produtividade,
        "leads_por_origem": {origem or "nao_informado": int(total) for origem, total in por_origem.items()},
        "atrasos": atrasados,
        "tempo_medio_ate_proposta_dias": round(sum(tempo_ate_proposta) / len(tempo_ate_proposta), 2)
        if tempo_ate_proposta
        else 0,
        "tempo_medio_ate_aceite_dias": round(sum(aceites) / len(aceites), 2) if aceites else 0,
        "taxa_pagamento": round(pagas / len(propostas), 4) if propostas else 0,
        "taxa_protocolo": round(protocoladas / len(propostas), 4) if propostas else 0,
        "atualizado_em": agora,
    }


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
        # Tarefa 3: desfecho estruturado ao converter/descartar.
        if dados.status == StatusLead.CONVERTIDO:
            lead.resultado = "ganho"
            lead.motivo_perda = None
            lead.motivo_perda_detalhe = None
        elif dados.status == StatusLead.DESCARTADO:
            lead.resultado = "perdido"
        else:
            lead.resultado = None
            lead.motivo_perda = None
            lead.motivo_perda_detalhe = None
    if lead.status == StatusLead.DESCARTADO and "motivo_perda" in dados.model_fields_set:
        if dados.motivo_perda and dados.motivo_perda not in MOTIVOS_PERDA:
            raise HTTPException(status_code=422, detail="Motivo de perda inválido")
        lead.motivo_perda = dados.motivo_perda
        lead.motivo_perda_detalhe = dados.motivo_perda_detalhe
        alteracoes["motivo_perda"] = dados.motivo_perda
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
    if "contato_id" in dados.model_fields_set:
        if dados.contato_id is not None:
            contato = (
                await session.execute(
                    select(Contato).where(
                        Contato.id == dados.contato_id,
                        Contato.organizacao_id == usuario.organizacao_id,
                    )
                )
            ).scalar_one_or_none()
            if contato is None:
                raise HTTPException(status_code=422, detail="Contato inválido")
            if lead.empresa_id is not None and contato.empresa_id != lead.empresa_id:
                raise HTTPException(status_code=422, detail="O contato não pertence à empresa desta oportunidade")
        alteracoes["contato_id"] = {"de": lead.contato_id, "para": dados.contato_id}
        lead.contato_id = dados.contato_id
    if "notas" in dados.model_fields_set:
        alteracoes["notas_atualizadas"] = True
        lead.notas = dados.notas
    if "proxima_acao_em" in dados.model_fields_set:
        alteracoes["proxima_acao_em"] = dados.proxima_acao_em.isoformat() if dados.proxima_acao_em else None
        lead.proxima_acao_em = dados.proxima_acao_em
        lembrete_manual = (
            await session.execute(
                select(LembreteCRM).where(
                    LembreteCRM.organizacao_id == usuario.organizacao_id,
                    LembreteCRM.lead_id == lead.id,
                    LembreteCRM.idempotency_key == f"manual:lead:{lead.id}",
                )
            )
        ).scalar_one_or_none()
        if dados.proxima_acao_em:
            if lembrete_manual is None:
                session.add(
                    LembreteCRM(
                        organizacao_id=usuario.organizacao_id,
                        lead_id=lead.id,
                        responsavel_id=lead.responsavel_id,
                        tipo="retorno",
                        prioridade="media",
                        titulo="Próxima ação do atendimento",
                        descricao="Ação definida no atendimento comercial.",
                        lembrar_em=dados.proxima_acao_em,
                        status="pendente",
                        criado_por=usuario.nome or "Operador",
                        criado_por_id=usuario.id,
                        idempotency_key=f"manual:lead:{lead.id}",
                    )
                )
            else:
                lembrete_manual.lembrar_em = dados.proxima_acao_em
                lembrete_manual.responsavel_id = lead.responsavel_id
                lembrete_manual.status = "pendente"
        elif lembrete_manual is not None:
            lembrete_manual.status = "cancelado"
    if dados.tags is not None:
        alteracoes["tags"] = dados.tags
        lead.tags = dados.tags
    if "documento" in dados.model_fields_set:
        alteracoes["documento_atualizado"] = True
        lead.documento = dados.documento
    # Tarefa 4: oportunidade aberta exige responsável e próxima ação.
    mexeu_crm = (
        dados.status is not None
        or "responsavel_id" in dados.model_fields_set
        or "proxima_acao_em" in dados.model_fields_set
    )
    aberta = lead.status not in (StatusLead.CONVERTIDO, StatusLead.DESCARTADO)
    if mexeu_crm and aberta:
        faltando = await aplicar_politica_oportunidade(session, lead, usuario.id)
        if faltando:
            raise HTTPException(
                status_code=422,
                detail=f"Oportunidade aberta exige {' e '.join(faltando)}.",
            )
    # Tarefa 2: ao mudar o status, sincroniza a fase do funil (só avança).
    if "status" in alteracoes:
        if await sincronizar_fase_por_status(session, lead, por=usuario.nome or "sistema"):
            alteracoes["fase"] = lead.fase
        # Automações disparadas por mudança de status (ex.: sem_retorno).
        await aplicar_regras_automacao(session, lead, "status", lead.status.value, por=usuario.nome or "sistema")
        registrar_evento_operacional(
            session,
            organizacao_id=usuario.organizacao_id,
            dominio="crm",
            tipo="crm.status_alterado",
            entidade_tipo="lead",
            entidade_id=lead.id,
            ator=usuario.ator,
            ator_id=usuario.id,
            payload=alteracoes["status"],
        )
    if dados.registrar_contato:
        agora = datetime.now(UTC)
        lead.ultimo_contato_em = agora
        alteracoes["contato_registrado"] = True
        pesquisa_id = (
            await session.execute(
                select(PesquisaMarca.id)
                .where(
                    PesquisaMarca.lead_id == lead.id,
                    PesquisaMarca.organizacao_id == usuario.organizacao_id,
                )
                .order_by(PesquisaMarca.criado_em.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        contato_registrado = ContatoLead(
            organizacao_id=usuario.organizacao_id,
            lead_id=lead.id,
            empresa_id=lead.empresa_id,
            pesquisa_id=pesquisa_id,
            operador_id=usuario.id,
            operador_nome=usuario.nome,
            canal=CanalContato.OUTRO,
            resultado="Atendimento atualizado",
            observacao=dados.notas or lead.notas,
            criado_em=agora,
        )
        session.add(contato_registrado)
        registrar_evento_operacional(
            session,
            organizacao_id=usuario.organizacao_id,
            dominio="crm",
            tipo="crm.interacao_registrada",
            entidade_tipo="lead",
            entidade_id=lead.id,
            ator=usuario.ator,
            ator_id=usuario.id,
            payload={
                "contato_id": contato_registrado.id,
                "canal": contato_registrado.canal.value,
                "resultado": contato_registrado.resultado,
            },
        )
    _auditar(session, usuario, request, "alterar", f"lead:{lead.id}", alteracoes)
    await session.commit()
    await session.refresh(lead)
    return _lead_response(lead, usuario)


class ContatoInput(BaseModel):
    canal: CanalContato = CanalContato.TELEFONE
    resultado: str | None = Field(default=None, max_length=150)
    observacao: str | None = Field(default=None, max_length=2000)
    pesquisa_id: str = Field(min_length=36, max_length=36)


def _contato_response(
    contato: ContatoLead,
    pesquisa_marca: str | None = None,
    empresa_nome: str | None = None,
) -> dict:
    return {
        "id": contato.id,
        "canal": contato.canal,
        "resultado": contato.resultado,
        "observacao": contato.observacao,
        "pesquisa_id": contato.pesquisa_id,
        "pesquisa_marca": pesquisa_marca,
        "empresa_id": contato.empresa_id,
        "empresa": empresa_nome,
        "operador": contato.operador_nome,
        "criado_em": contato.criado_em,
    }


async def _lead_do_operador(lead_id: int, session: AsyncSession, usuario: UsuarioAutenticado) -> Lead:
    lead = await session.get(Lead, lead_id)
    if lead is None or lead.organizacao_id != usuario.organizacao_id:
        raise HTTPException(status_code=404, detail="Lead nao encontrado")
    return lead


@router.get("/v1/admin/leads/{lead_id}/contatos")
async def listar_contatos(
    lead_id: int,
    session: SessionDep,
    usuario: LeadsViewDep,
    pesquisa_id: str | None = Query(default=None, max_length=36),
) -> dict:
    await _lead_do_operador(lead_id, session, usuario)
    filtros = [ContatoLead.lead_id == lead_id]
    if pesquisa_id:
        filtros.append(ContatoLead.pesquisa_id == pesquisa_id)
    contatos = (
        await session.execute(
            select(ContatoLead, PesquisaMarca.marca, EmpresaCRM.nome)
            .outerjoin(PesquisaMarca, PesquisaMarca.id == ContatoLead.pesquisa_id)
            .outerjoin(EmpresaCRM, EmpresaCRM.id == ContatoLead.empresa_id)
            .where(*filtros)
            .order_by(ContatoLead.criado_em.desc())
        )
    ).all()
    return {
        "total": len(contatos),
        "contatos": [_contato_response(c, marca, empresa) for c, marca, empresa in contatos],
    }


@router.post("/v1/admin/leads/{lead_id}/contatos", status_code=status.HTTP_201_CREATED)
async def registrar_contato_lead(
    lead_id: int,
    dados: ContatoInput,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    lead = await _lead_do_operador(lead_id, session, usuario)
    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(
                PesquisaMarca.id == dados.pesquisa_id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
                PesquisaMarca.lead_id == lead_id,
            )
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(
            status_code=422,
            detail="A pesquisa informada nao pertence a este contato e empresa",
        )
    if lead.empresa_id != pesquisa.empresa_id:
        raise HTTPException(status_code=422, detail="Pesquisa vinculada a outra empresa")
    contato = ContatoLead(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead_id,
        empresa_id=lead.empresa_id,
        pesquisa_id=pesquisa.id,
        operador_id=usuario.id,
        operador_nome=usuario.nome,
        canal=dados.canal,
        resultado=dados.resultado,
        observacao=dados.observacao,
    )
    session.add(contato)
    lead.ultimo_contato_em = datetime.now(UTC)
    registrar_evento_operacional(
        session,
        organizacao_id=usuario.organizacao_id,
        dominio="crm",
        tipo="crm.interacao_registrada",
        entidade_tipo="lead",
        entidade_id=lead_id,
        ator=usuario.ator,
        ator_id=usuario.id,
        payload={
            "contato_id": contato.id,
            "pesquisa_id": pesquisa.id,
            "canal": contato.canal.value,
            "resultado": contato.resultado,
        },
    )
    _auditar(session, usuario, request, "registrar_contato", f"lead:{lead_id}", {"canal": dados.canal})
    await session.commit()
    await session.refresh(contato)
    return _contato_response(contato, pesquisa.marca, lead.empresa)


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
    exclusao_pendente = (
        select(SolicitacaoExclusaoPesquisa.status)
        .where(
            SolicitacaoExclusaoPesquisa.pesquisa_id == PesquisaMarca.id,
            SolicitacaoExclusaoPesquisa.status == "pendente",
        )
        .limit(1)
        .scalar_subquery()
    )
    linhas = (
        await session.execute(
            select(
                PesquisaMarca,
                AvaliacaoRiscoMarca.nivel,
                AvaliacaoRiscoMarca.pontuacao,
                relatorio_existe.label("relatorio_disponivel"),
                exclusao_pendente.label("exclusao_status"),
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
        _resumo_pesquisa(pesquisa, nivel, pontuacao, bool(disponivel), exclusao_status)
        for pesquisa, nivel, pontuacao, disponivel, exclusao_status in linhas
    ]
    base = _lead_response(lead, usuario, pesquisas)
    return LeadDetalheResponse.model_validate(base.model_dump())


@router.get("/v1/admin/leads/{lead_id}/funil")
async def funil_lead(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    historico = (
        await session.execute(
            select(HistoricoFaseLead.fase, HistoricoFaseLead.entrou_em, HistoricoFaseLead.por)
            .where(HistoricoFaseLead.lead_id == lead.id)
            .order_by(HistoricoFaseLead.entrou_em)
        )
    ).all()
    return {
        "fase": lead.fase,
        "ordem": list(ORDEM_FASE_LEAD),
        "historico": [{"fase": f, "entrou_em": e, "por": p} for f, e, p in historico],
    }


class FaseLeadInput(BaseModel):
    fase: FaseLead


@router.post("/v1/admin/leads/{lead_id}/fase")
async def definir_fase_lead(
    lead_id: int,
    dados: FaseLeadInput,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if dados.fase.value in {"protocolo_inpi", "processo_inpi"}:
        documentos = (
            (
                await session.execute(
                    select(DocumentoLead).where(
                        DocumentoLead.lead_id == lead.id,
                        DocumentoLead.organizacao_id == lead.organizacao_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        por_tipo = {documento.tipo: documento for documento in documentos}
        obrigatorios = {documento.tipo for documento in documentos if documento.obrigatorio}
        obrigatorios.add("procuracao")
        pendencias = sorted(
            tipo
            for tipo in obrigatorios
            if tipo not in por_tipo
            or por_tipo[tipo].status not in DOCUMENTOS_VALIDOS
            or (por_tipo[tipo].validade_em is not None and por_tipo[tipo].validade_em < datetime.now(UTC).date())
        )
        if pendencias:
            raise HTTPException(
                status_code=422,
                detail=f"Etapa bloqueada. Documentos obrigatórios pendentes: {', '.join(pendencias)}.",
            )
    mudou = await avancar_fase_lead(
        session,
        lead,
        dados.fase.value,
        por=getattr(usuario, "nome", None) or "operador",
        forcar=True,
    )
    if mudou:
        _auditar(
            session,
            usuario,
            request,
            "fase_lead",
            f"lead:{lead.id}",
            {"fase": dados.fase.value},
        )
    await session.commit()
    return {"fase": lead.fase}


class DocumentoInput(BaseModel):
    tipo: str
    numero: str | None = Field(default=None, max_length=60)
    data: date | None = None
    status: str = Field(default="pendente", max_length=20)
    observacoes: str | None = Field(default=None, max_length=2000)
    obrigatorio: bool = False
    validade_em: date | None = None


class DocumentosInput(BaseModel):
    documentos: list[DocumentoInput] = Field(default_factory=list)


class PropostaInput(BaseModel):
    pesquisa_id: str | None = Field(default=None, min_length=36, max_length=36)
    pesquisa_ids: list[str] = Field(default_factory=list, max_length=50)
    validade_em: date | None = None
    marca: str | None = Field(default=None, max_length=200)
    classes: str | None = Field(default=None, max_length=200)
    escopo: str = Field(default="Registro de marca no INPI", min_length=5, max_length=4000)
    honorarios: Decimal | None = Field(default=None, ge=0)
    taxa_gru: Decimal | None = Field(default=None, ge=0)
    condicoes_pagamento: str | None = Field(default=None, max_length=2000)
    observacoes: str | None = Field(default=None, max_length=4000)


# Valores padrão do rascunho comercial quando a proposta é criada diretamente
# pelo funil, sem interromper o usuário para preencher um formulário.
HONORARIOS_PROPOSTA_PADRAO = Decimal("1500.00")
TAXA_GRU_PROPOSTA_PADRAO = Decimal("415.00")
CONDICOES_PROPOSTA_PADRAO = "50% na contratação e 50% no protocolo"


class PropostaStatusInput(BaseModel):
    status: Literal["rascunho", "enviada", "visualizada", "aceita", "recusada", "expirada", "cancelada"]


class PropostaPagamentoInput(BaseModel):
    status: Literal["pendente", "confirmado", "parcial", "cancelado"]
    confirmado_em: datetime | None = None


class PropostaProtocoloInput(BaseModel):
    responsavel_protocolo_id: int
    protocolo_numero: str | None = Field(default=None, max_length=80)
    comprovante_id: int | None = None
    motivo_atraso: str | None = Field(default=None, max_length=2000)


def _prazo_sla_24h(inicio: datetime) -> datetime:
    """Prazo operacional explícito de 24 horas corridas após liberar o protocolo."""
    return inicio + timedelta(hours=24)


def _atualizar_sla_proposta(proposta: PropostaComercial, agora: datetime | None = None) -> str:
    agora = agora or datetime.now(UTC)
    if proposta.protocolo_em:
        proposta.sla_status = "protocolado"
    elif proposta.status != "aceita":
        proposta.sla_status = "aguardando_aceite"
    elif proposta.pagamento_status != "confirmado":
        proposta.sla_status = "aguardando_pagamento"
    elif not proposta.sla_inicio_em:
        proposta.sla_status = "aguardando_documentos"
    elif proposta.sla_prazo_em and agora > proposta.sla_prazo_em:
        proposta.sla_status = "vencido"
    else:
        proposta.sla_status = "em_prazo"
    return proposta.sla_status


DOCUMENTOS_VALIDOS = {"validado", "recebido", "aprovado"}


async def _pendencias_documentos(session: AsyncSession, proposta: PropostaComercial) -> list[str]:
    documentos = (
        (
            await session.execute(
                select(DocumentoLead).where(
                    DocumentoLead.lead_id == proposta.lead_id,
                    DocumentoLead.organizacao_id == proposta.organizacao_id,
                )
            )
        )
        .scalars()
        .all()
    )
    por_tipo = {item.tipo: item for item in documentos}
    obrigatorios = {item.tipo for item in documentos if item.obrigatorio}
    # A procuração é o mínimo obrigatório para liberar o protocolo, mesmo
    # quando o operador ainda não marcou o metadado ``obrigatorio``.
    obrigatorios.add("procuracao")
    return sorted(
        tipo
        for tipo in obrigatorios
        if (
            tipo not in por_tipo
            or por_tipo[tipo].status not in DOCUMENTOS_VALIDOS
            or (por_tipo[tipo].validade_em is not None and por_tipo[tipo].validade_em < datetime.now(UTC).date())
        )
    )


async def _documentacao_protocolavel(session: AsyncSession, proposta: PropostaComercial) -> bool:
    return not await _pendencias_documentos(session, proposta)


async def _lead_da_org(session: AsyncSession, lead_id: int, organizacao_id: int) -> int:
    lead = (
        await session.execute(select(Lead.id).where(Lead.id == lead_id, Lead.organizacao_id == organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    return lead


@router.get("/v1/admin/leads/{lead_id}/documentos")
async def listar_documentos_lead(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    await _lead_da_org(session, lead_id, usuario.organizacao_id)
    docs = (
        (
            await session.execute(
                select(DocumentoLead).where(
                    DocumentoLead.lead_id == lead_id,
                    DocumentoLead.organizacao_id == usuario.organizacao_id,
                )
            )
        )
        .scalars()
        .all()
    )
    por_tipo = {d.tipo: d for d in docs}
    return {
        "tipos": list(TIPOS_DOCUMENTO_LEAD),
        "documentos": [
            {
                "tipo": t,
                "numero": por_tipo[t].numero if t in por_tipo else None,
                "data": por_tipo[t].data if t in por_tipo else None,
                "status": por_tipo[t].status if t in por_tipo else "pendente",
                "observacoes": por_tipo[t].observacoes if t in por_tipo else None,
                "versao": por_tipo[t].versao if t in por_tipo else None,
                "hash": por_tipo[t].hash_documento if t in por_tipo else None,
                "obrigatorio": por_tipo[t].obrigatorio if t in por_tipo else False,
                "validade_em": por_tipo[t].validade_em if t in por_tipo else None,
                "assinado_em": por_tipo[t].assinado_em if t in por_tipo else None,
            }
            for t in TIPOS_DOCUMENTO_LEAD
        ],
    }


@router.put("/v1/admin/leads/{lead_id}/documentos")
async def salvar_documentos_lead(
    lead_id: int,
    dados: DocumentosInput,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    await _lead_da_org(session, lead_id, usuario.organizacao_id)
    existentes = {
        d.tipo: d
        for d in (
            await session.execute(
                select(DocumentoLead).where(
                    DocumentoLead.lead_id == lead_id,
                    DocumentoLead.organizacao_id == usuario.organizacao_id,
                )
            )
        )
        .scalars()
        .all()
    }
    for item in dados.documentos:
        if item.tipo not in TIPOS_DOCUMENTO_LEAD:
            continue
        numero = (item.numero or "").strip() or None
        obs = (item.observacoes or "").strip() or None
        status = (item.status or "pendente").strip() or "pendente"
        vazio = not numero and item.data is None and not obs and status == "pendente" and not item.obrigatorio
        atual = existentes.get(item.tipo)
        if atual is None:
            if vazio:
                continue
            session.add(
                DocumentoLead(
                    organizacao_id=usuario.organizacao_id,
                    lead_id=lead_id,
                    tipo=item.tipo,
                    numero=numero,
                    data=item.data,
                    status=status,
                    observacoes=obs,
                    obrigatorio=item.obrigatorio,
                    validade_em=item.validade_em,
                )
            )
        else:
            mudou_conteudo = (
                atual.numero != numero
                or atual.data != item.data
                or atual.observacoes != obs
                or atual.status != status
                or atual.obrigatorio != item.obrigatorio
                or atual.validade_em != item.validade_em
            )
            if mudou_conteudo:
                session.add(
                    VersaoDocumentoLead(
                        organizacao_id=atual.organizacao_id,
                        documento_id=atual.id,
                        versao=atual.versao,
                        hash_documento=atual.hash_documento
                        or hashlib.sha256(
                            f"{atual.tipo}|{atual.numero or ''}|{atual.data or ''}|{atual.status}|{atual.observacoes or ''}|{atual.validade_em or ''}".encode()
                        ).hexdigest(),
                        conteudo={
                            "tipo": atual.tipo,
                            "numero": atual.numero,
                            "data": atual.data.isoformat() if atual.data else None,
                            "status": atual.status,
                            "observacoes": atual.observacoes,
                            "obrigatorio": atual.obrigatorio,
                            "validade_em": atual.validade_em.isoformat() if atual.validade_em else None,
                        },
                    )
                )
                atual.versao += 1
                atual.hash_documento = None
                atual.assinado_em = None
                atual.assinado_ip_hash = None
                atual.assinado_por_cliente_id = None
            atual.numero = numero
            atual.data = item.data
            atual.status = status
            atual.observacoes = obs
            atual.obrigatorio = item.obrigatorio
            atual.validade_em = item.validade_em
    propostas_aguardando = (
        (
            await session.execute(
                select(PropostaComercial).where(
                    PropostaComercial.lead_id == lead_id,
                    PropostaComercial.organizacao_id == usuario.organizacao_id,
                    PropostaComercial.status == "aceita",
                    PropostaComercial.pagamento_status == "confirmado",
                    PropostaComercial.sla_inicio_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for proposta in propostas_aguardando:
        if await _documentacao_protocolavel(session, proposta):
            proposta.sla_inicio_em = datetime.now(UTC)
            proposta.sla_prazo_em = _prazo_sla_24h(proposta.sla_inicio_em)
            proposta.sla_status = "em_prazo"
    _auditar(session, usuario, request, "documentos_lead", f"lead:{lead_id}", {})
    await session.commit()
    return {"ok": True}


@router.get("/v1/admin/leads/{lead_id}/documentos/{documento_id}/versoes")
async def listar_versoes_documento_lead(
    lead_id: int, documento_id: int, session: SessionDep, usuario: LeadsViewDep
) -> dict:
    await _lead_da_org(session, lead_id, usuario.organizacao_id)
    documento = (
        await session.execute(
            select(DocumentoLead).where(
                DocumentoLead.id == documento_id,
                DocumentoLead.lead_id == lead_id,
                DocumentoLead.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if documento is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    versoes = (
        (
            await session.execute(
                select(VersaoDocumentoLead)
                .where(
                    VersaoDocumentoLead.documento_id == documento.id,
                    VersaoDocumentoLead.organizacao_id == usuario.organizacao_id,
                )
                .order_by(VersaoDocumentoLead.versao.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "documento_id": documento.id,
        "versao_atual": documento.versao,
        "versoes": [
            {
                "id": item.id,
                "versao": item.versao,
                "hash": item.hash_documento,
                "criado_por_tipo": item.criado_por_tipo,
                "criado_por_id": item.criado_por_id,
                "criado_em": item.criado_em,
                "conteudo": item.conteudo,
            }
            for item in versoes
        ],
    }


# Itens sugeridos por etapa — aplicados sob demanda (botão "aplicar padrão").
CHECKLIST_PADRAO_FASE: dict[str, tuple[str, ...]] = {
    "contato_inicial": ("Registrar dados do cliente", "Entender a necessidade da marca"),
    "relatorio_enviado": ("Gerar relatório de viabilidade", "Enviar relatório ao cliente"),
    "proposta_enviada": ("Elaborar proposta comercial", "Enviar proposta ao cliente"),
    "proposta_aceita": ("Confirmar aceite da proposta", "Coletar dados para a procuração"),
    "pagamento_realizado": ("Emitir cobrança", "Confirmar pagamento"),
    "protocolo_inpi": (
        "Procuração assinada",
        "GRU emitida",
        "GRU paga",
        "Protocolizar pedido no e-Marcas",
    ),
    "processo_inpi": ("Registrar número do processo no INPI", "Informar o cliente do protocolo"),
}


class ChecklistItemInput(BaseModel):
    descricao: str = Field(min_length=1, max_length=300)


class ChecklistToggleInput(BaseModel):
    concluido: bool


async def _checklist_itens(
    session: AsyncSession, lead_id: int, organizacao_id: int, fase: str
) -> list[ChecklistFaseLead]:
    return list(
        (
            await session.execute(
                select(ChecklistFaseLead)
                .where(
                    ChecklistFaseLead.lead_id == lead_id,
                    ChecklistFaseLead.organizacao_id == organizacao_id,
                    ChecklistFaseLead.fase == fase,
                )
                .order_by(ChecklistFaseLead.ordem, ChecklistFaseLead.id)
            )
        )
        .scalars()
        .all()
    )


@router.get("/v1/admin/leads/{lead_id}/checklist")
async def obter_checklist_fase(
    lead_id: int, session: SessionDep, usuario: LeadsViewDep, fase: str | None = None
) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    fase_alvo = fase or lead.fase
    itens = await _checklist_itens(session, lead_id, usuario.organizacao_id, fase_alvo)
    return {
        "fase": fase_alvo,
        "tem_padrao": fase_alvo in CHECKLIST_PADRAO_FASE and not itens,
        "itens": [{"id": i.id, "descricao": i.descricao, "concluido": i.concluido} for i in itens],
    }


@router.post("/v1/admin/leads/{lead_id}/checklist/padrao")
async def aplicar_checklist_padrao(
    lead_id: int, session: SessionDep, usuario: LeadsManageDep, fase: str | None = None
) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    fase_alvo = fase or lead.fase
    if await _checklist_itens(session, lead_id, usuario.organizacao_id, fase_alvo):
        raise HTTPException(status_code=400, detail="A etapa já tem checklist.")
    for i, descricao in enumerate(CHECKLIST_PADRAO_FASE.get(fase_alvo, ())):
        session.add(
            ChecklistFaseLead(
                organizacao_id=usuario.organizacao_id,
                lead_id=lead_id,
                fase=fase_alvo,
                descricao=descricao,
                ordem=i,
            )
        )
    await session.commit()
    return {"ok": True}


@router.post("/v1/admin/leads/{lead_id}/checklist", status_code=status.HTTP_201_CREATED)
async def adicionar_checklist_item(
    lead_id: int,
    dados: ChecklistItemInput,
    session: SessionDep,
    usuario: LeadsManageDep,
    fase: str | None = None,
) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    fase_alvo = fase or lead.fase
    ordem = (
        await session.execute(
            select(func.coalesce(func.max(ChecklistFaseLead.ordem), -1)).where(
                ChecklistFaseLead.lead_id == lead_id,
                ChecklistFaseLead.organizacao_id == usuario.organizacao_id,
                ChecklistFaseLead.fase == fase_alvo,
            )
        )
    ).scalar_one() + 1
    item = ChecklistFaseLead(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead_id,
        fase=fase_alvo,
        descricao=dados.descricao.strip(),
        ordem=ordem,
    )
    session.add(item)
    await session.commit()
    return {"id": item.id}


@router.patch("/v1/admin/checklist-fase/{item_id}")
async def alternar_checklist_item(
    item_id: int, dados: ChecklistToggleInput, session: SessionDep, usuario: LeadsManageDep
) -> dict:
    item = (
        await session.execute(
            select(ChecklistFaseLead).where(
                ChecklistFaseLead.id == item_id,
                ChecklistFaseLead.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    item.concluido = dados.concluido
    await session.commit()
    return {"ok": True}


@router.delete("/v1/admin/checklist-fase/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_checklist_item(item_id: int, session: SessionDep, usuario: LeadsManageDep) -> Response:
    item = (
        await session.execute(
            select(ChecklistFaseLead).where(
                ChecklistFaseLead.id == item_id,
                ChecklistFaseLead.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    await session.delete(item)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Propostas comerciais por lead ----------------------------------------


def _proposta_dict(proposta: PropostaComercial, org: Organizacao | None = None) -> dict:
    branding = (org.branding or {}) if org else {}
    return {
        "id": proposta.id,
        "lead_id": proposta.lead_id,
        "pesquisa_id": proposta.pesquisa_id,
        "numero": proposta.numero,
        "versao": proposta.versao,
        "status": proposta.status,
        "validade_em": proposta.validade_em,
        "marca": proposta.marca,
        "classes": proposta.classes,
        "escopo": proposta.escopo,
        "honorarios": proposta.honorarios,
        "taxa_gru": proposta.taxa_gru,
        "total": (proposta.honorarios or 0) + (proposta.taxa_gru or 0),
        "condicoes_pagamento": proposta.condicoes_pagamento,
        "observacoes": proposta.observacoes,
        "pesquisas": (proposta.dados or {}).get("pesquisas") or [],
        "cliente": {"nome": (proposta.dados or {}).get("cliente") or "cliente"},
        "enviado_em": proposta.enviado_em,
        "aceito_em": proposta.aceito_em,
        "public_aceito_ip_registrado": bool(proposta.public_aceito_ip_hash),
        "pagamento_status": proposta.pagamento_status,
        "pagamento_confirmado_em": proposta.pagamento_confirmado_em,
        "pagamento_confirmado_por": proposta.pagamento_confirmado_por,
        "sla_inicio_em": proposta.sla_inicio_em,
        "sla_prazo_em": proposta.sla_prazo_em,
        "sla_status": _atualizar_sla_proposta(proposta),
        "responsavel_protocolo_id": proposta.responsavel_protocolo_id,
        "protocolo_numero": proposta.protocolo_numero,
        "protocolo_em": proposta.protocolo_em,
        "protocolo_motivo_atraso": proposta.protocolo_motivo_atraso,
        "protocolo_comprovante_id": proposta.protocolo_comprovante_id,
        "criado_em": proposta.criado_em,
        # Propostas exibem somente o nome fantasia institucional.
        "empresa": {"nome": "Zé Registra"},
        "configuracao": branding.get("proposta") or {},
    }


@router.get("/v1/admin/leads/{lead_id}/propostas")
async def listar_propostas(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    lead = await _lead_da_org(session, lead_id, usuario.organizacao_id)
    org = await session.get(Organizacao, usuario.organizacao_id)
    itens = (
        (
            await session.execute(
                select(PropostaComercial)
                .where(
                    PropostaComercial.lead_id == lead,
                    PropostaComercial.organizacao_id == usuario.organizacao_id,
                )
                .order_by(desc(PropostaComercial.criado_em))
            )
        )
        .scalars()
        .all()
    )
    return {"propostas": [_proposta_dict(item, org) for item in itens]}


@router.post("/v1/admin/leads/{lead_id}/propostas", status_code=status.HTTP_201_CREATED)
async def criar_proposta(lead_id: int, dados: PropostaInput, session: SessionDep, usuario: LeadsManageDep) -> dict:
    lead = await _lead_da_org(session, lead_id, usuario.organizacao_id)
    lead_obj = (
        await session.execute(select(Lead).where(Lead.id == lead, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one()
    ids = list(dict.fromkeys([item for item in dados.pesquisa_ids if item]))
    if dados.pesquisa_id and dados.pesquisa_id not in ids:
        ids.insert(0, dados.pesquisa_id)
    pesquisa_query = select(PesquisaMarca).where(
        PesquisaMarca.lead_id == lead,
        PesquisaMarca.organizacao_id == usuario.organizacao_id,
    )
    if ids:
        pesquisa_query = pesquisa_query.where(PesquisaMarca.id.in_(ids))
    pesquisas = list((await session.execute(pesquisa_query.order_by(PesquisaMarca.criado_em.desc()))).scalars())
    if ids and len(pesquisas) != len(ids):
        raise HTTPException(
            status_code=422,
            detail="A pesquisa informada não pertence a esta oportunidade.",
        )
    pesquisa = pesquisas[0] if pesquisas else None
    proposta = PropostaComercial(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead,
        pesquisa_id=pesquisa.id if pesquisa else None,
        numero="TEMP",
        validade_em=dados.validade_em,
        marca=dados.marca or (pesquisa.marca if pesquisa else None),
        classes=dados.classes or (pesquisa.classe_nice if pesquisa else None),
        escopo=dados.escopo.strip(),
        honorarios=dados.honorarios if dados.honorarios is not None else HONORARIOS_PROPOSTA_PADRAO,
        taxa_gru=dados.taxa_gru if dados.taxa_gru is not None else TAXA_GRU_PROPOSTA_PADRAO,
        condicoes_pagamento=dados.condicoes_pagamento or CONDICOES_PROPOSTA_PADRAO,
        observacoes=dados.observacoes,
        criado_por=usuario.id,
        dados={
            "cliente": lead_obj.nome,
            "email": lead_obj.email,
            "pesquisas": [{"id": item.id, "marca": item.marca, "classes": item.classe_nice} for item in pesquisas],
            "protocolo_prazo": "24 horas úteis",
        },
    )
    session.add(proposta)
    await session.flush()
    proposta.numero = f"PROP-{datetime.now(UTC).year}-{proposta.id:06d}"
    await session.commit()
    org = await session.get(Organizacao, usuario.organizacao_id)
    return _proposta_dict(proposta, org)


@router.post("/v1/admin/propostas/{proposta_id}/nova-versao", status_code=status.HTTP_201_CREATED)
async def criar_nova_versao_proposta(
    proposta_id: int,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    anterior = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    nova = PropostaComercial(
        organizacao_id=anterior.organizacao_id,
        lead_id=anterior.lead_id,
        pesquisa_id=anterior.pesquisa_id,
        numero="TEMP",
        versao=anterior.versao + 1,
        status="rascunho",
        validade_em=anterior.validade_em,
        marca=anterior.marca,
        classes=anterior.classes,
        escopo=anterior.escopo,
        honorarios=anterior.honorarios,
        taxa_gru=anterior.taxa_gru,
        condicoes_pagamento=anterior.condicoes_pagamento,
        observacoes=anterior.observacoes,
        dados=dict(anterior.dados or {}),
        criado_por=usuario.id,
    )
    session.add(nova)
    await session.flush()
    nova.numero = f"PROP-{datetime.now(UTC).year}-{nova.id:06d}"
    _auditar(
        session,
        usuario,
        request,
        "nova_versao_proposta",
        f"proposta:{nova.id}",
        {"origem_id": anterior.id, "versao": nova.versao},
    )
    await session.commit()
    return _proposta_dict(nova, await session.get(Organizacao, usuario.organizacao_id))


@router.patch("/v1/admin/propostas/{proposta_id}/status")
async def atualizar_status_proposta(
    proposta_id: int,
    dados: PropostaStatusInput,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    agora = datetime.now(UTC)
    proposta.status = dados.status
    if dados.status == "enviada":
        proposta.enviado_em = agora
        lead = (
            await session.execute(
                select(Lead).where(Lead.id == proposta.lead_id, Lead.organizacao_id == usuario.organizacao_id)
            )
        ).scalar_one_or_none()
        if lead and lead.fase:
            await avancar_fase_lead(session, lead, "proposta_enviada", usuario.nome or "sistema")
            await aplicar_regras_automacao(session, lead, "fase", "proposta_enviada", usuario.nome or "sistema")
    elif dados.status == "aceita":
        proposta.aceito_em = agora
        proposta.sla_status = "aguardando_pagamento"
        lead = (
            await session.execute(
                select(Lead).where(Lead.id == proposta.lead_id, Lead.organizacao_id == usuario.organizacao_id)
            )
        ).scalar_one_or_none()
        if lead and lead.fase:
            await avancar_fase_lead(session, lead, "proposta_aceita", usuario.nome or "sistema")
            await aplicar_regras_automacao(session, lead, "fase", "proposta_aceita", usuario.nome or "sistema")
    _auditar(
        session,
        usuario,
        request,
        "status_proposta",
        f"proposta:{proposta.id}",
        {"status": dados.status},
    )
    await session.commit()
    org = await session.get(Organizacao, usuario.organizacao_id)
    return _proposta_dict(proposta, org)


async def _proposta_da_org(session: AsyncSession, proposta_id: int, organizacao_id: int) -> PropostaComercial:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    return proposta


@router.patch("/v1/admin/propostas/{proposta_id}/pagamento")
async def atualizar_pagamento_proposta(
    proposta_id: int,
    dados: PropostaPagamentoInput,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    proposta.pagamento_status = dados.status
    proposta.pagamento_confirmado_em = (
        (dados.confirmado_em or datetime.now(UTC)) if dados.status == "confirmado" else None
    )
    if dados.status == "confirmado":
        proposta.pagamento_confirmado_por_id = usuario.id
        proposta.pagamento_confirmado_por = usuario.ator
        proposta.pagamento_confirmado_ip_hash = hash_ip(cliente_ip(request))
    else:
        proposta.pagamento_confirmado_por_id = None
        proposta.pagamento_confirmado_por = None
        proposta.pagamento_confirmado_ip_hash = None
    documentos_ok = await _documentacao_protocolavel(session, proposta)
    if (
        proposta.pagamento_status == "confirmado"
        and proposta.status == "aceita"
        and not proposta.sla_inicio_em
        and documentos_ok
    ):
        proposta.sla_inicio_em = proposta.pagamento_confirmado_em
        proposta.sla_prazo_em = _prazo_sla_24h(proposta.sla_inicio_em)
    if proposta.pagamento_status == "confirmado" and proposta.status == "aceita" and not documentos_ok:
        proposta.sla_status = "aguardando_documentos"
    _atualizar_sla_proposta(proposta)
    _auditar(
        session,
        usuario,
        request,
        "pagamento_proposta",
        f"proposta:{proposta.id}",
        {"status": dados.status},
    )
    await session.commit()
    return _proposta_dict(proposta, await session.get(Organizacao, usuario.organizacao_id))


@router.patch("/v1/admin/propostas/{proposta_id}/protocolo")
async def registrar_protocolo_proposta(
    proposta_id: int,
    dados: PropostaProtocoloInput,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    responsavel = (
        await session.execute(
            select(UsuarioOperacoes).where(
                UsuarioOperacoes.id == dados.responsavel_protocolo_id,
                UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if responsavel is None:
        raise HTTPException(status_code=422, detail="Responsável pelo protocolo inválido")
    numero = (dados.protocolo_numero or "").strip() or None
    motivo = (dados.motivo_atraso or "").strip() or None
    agora = datetime.now(UTC)
    if not numero and not motivo:
        raise HTTPException(status_code=422, detail="Informe o número do protocolo ou o motivo do atraso")
    if numero:
        if proposta.status != "aceita" or proposta.pagamento_status != "confirmado":
            raise HTTPException(
                status_code=422,
                detail="O protocolo exige proposta aceita e pagamento confirmado.",
            )
        pendencias = await _pendencias_documentos(session, proposta)
        if pendencias:
            raise HTTPException(
                status_code=422,
                detail=f"Existem documentos pendentes: {', '.join(pendencias)}.",
            )
        if dados.comprovante_id is None:
            raise HTTPException(status_code=422, detail="Anexe o comprovante do protocolo.")
        if proposta.sla_prazo_em and agora > proposta.sla_prazo_em and not motivo:
            raise HTTPException(
                status_code=422,
                detail="Informe o motivo obrigatório do atraso antes de registrar o protocolo.",
            )
    if dados.comprovante_id is not None:
        comprovante = (
            await session.execute(
                select(DocumentoLead).where(
                    DocumentoLead.id == dados.comprovante_id,
                    DocumentoLead.lead_id == proposta.lead_id,
                    DocumentoLead.organizacao_id == usuario.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if comprovante is None:
            raise HTTPException(status_code=422, detail="Comprovante não encontrado para este lead")
    proposta.responsavel_protocolo_id = responsavel.id
    proposta.protocolo_numero = numero
    proposta.protocolo_motivo_atraso = motivo
    proposta.protocolo_comprovante_id = dados.comprovante_id
    proposta.protocolo_em = agora if numero else None
    _atualizar_sla_proposta(proposta)
    _auditar(
        session,
        usuario,
        request,
        "protocolo_proposta",
        f"proposta:{proposta.id}",
        {
            "protocolo_numero": numero,
            "motivo_atraso": motivo,
            "responsavel_id": responsavel.id,
        },
    )
    await session.commit()
    return _proposta_dict(proposta, await session.get(Organizacao, usuario.organizacao_id))


@router.get("/v1/admin/propostas/{proposta_id}/sla")
async def obter_sla_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    status_sla = _atualizar_sla_proposta(proposta)
    pendencias = await _pendencias_documentos(session, proposta)
    return {
        "proposta_id": proposta.id,
        "status": status_sla,
        "inicio_em": proposta.sla_inicio_em,
        "prazo_em": proposta.sla_prazo_em,
        "responsavel_id": proposta.responsavel_protocolo_id,
        "protocolo_numero": proposta.protocolo_numero,
        "protocolo_em": proposta.protocolo_em,
        "motivo_atraso": proposta.protocolo_motivo_atraso,
        "aceito_em": proposta.aceito_em,
        "pagamento_confirmado_em": proposta.pagamento_confirmado_em,
        "pagamento_confirmado_por": proposta.pagamento_confirmado_por,
        "public_aceito_ip_registrado": bool(proposta.public_aceito_ip_hash),
        "documentos_pendentes": pendencias,
    }


@router.get("/v1/admin/propostas/{proposta_id}/assinaturas")
async def listar_assinaturas_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    assinaturas = (
        (
            await session.execute(
                select(AssinaturaPropostaComercial)
                .where(
                    AssinaturaPropostaComercial.proposta_id == proposta.id,
                    AssinaturaPropostaComercial.organizacao_id == usuario.organizacao_id,
                )
                .order_by(AssinaturaPropostaComercial.assinado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "proposta_id": proposta.id,
        "assinaturas": [
            {
                "id": item.id,
                "versao": item.versao,
                "hash": item.hash_documento,
                "ip_registrado": bool(item.ip_hash),
                "assinado_em": item.assinado_em,
                "provedor": item.provedor,
            }
            for item in assinaturas
        ],
    }


@router.get("/v1/admin/propostas/{proposta_id}/pendencias")
async def obter_pendencias_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    pendencias = await _pendencias_documentos(session, proposta)
    return {
        "proposta_id": proposta.id,
        "status": proposta.status,
        "pagamento_status": proposta.pagamento_status,
        "documentos_pendentes": pendencias,
        "sla_liberado": not pendencias and proposta.status == "aceita" and proposta.pagamento_status == "confirmado",
    }


@router.get("/v1/admin/propostas/{proposta_id}/documento")
async def documento_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    org = await session.get(Organizacao, usuario.organizacao_id)
    return {
        "proposta": _proposta_dict(proposta, org),
        "texto": (
            f"PROPOSTA DE REGISTRO DE MARCA\\n\\n{org.nome}\\n{(org.branding or {}).get('cnpj', '')}\\n"
            f"{(org.branding or {}).get('endereco', '')}\\n"
            f"Telefone: {(org.branding or {}).get('telefone', org.telefone_contato or '')}\\n"
            f"E-mail: {(org.branding or {}).get('email', org.email_contato or '')}\\n"
            f"Site: {(org.branding or {}).get('site', '')}\\n\\n"
            f"Cliente: {proposta.dados.get('cliente', '')}\\nMarca: {proposta.marca or 'A definir'}\\n"
            f"Classes: {proposta.classes or 'A definir'}\\n\\n{proposta.escopo}\\n\\n"
            "Após aceite, pagamento e recebimento dos documentos, o protocolo será realizado em até 24 horas úteis.\\n"
            "O protocolo não representa garantia de concessão; a decisão pertence ao INPI."
        ),
    }


async def _proposta_por_token(session: AsyncSession, token: str) -> PropostaComercial | None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    proposta = (
        await session.execute(select(PropostaComercial).where(PropostaComercial.public_token_hash == digest))
    ).scalar_one_or_none()
    if proposta is None or not proposta.public_token_expira_em or proposta.public_token_expira_em < datetime.now(UTC):
        return None
    # Link publico chega sem sessao de operador -- resolve o tenant a partir da
    # propria proposta antes de qualquer leitura/escrita adicional protegida por RLS.
    await aplicar_contexto_tenant(session, proposta.organizacao_id)
    return proposta


@router.get("/v1/admin/propostas/{proposta_id}/pdf")
async def pdf_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> Response:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    org = await session.get(Organizacao, usuario.organizacao_id)
    pdf = gerar_pdf_proposta(_proposta_dict(proposta, org))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="proposta-{proposta.numero}.pdf"'},
    )


@router.post("/v1/admin/propostas/{proposta_id}/link")
async def criar_link_proposta(proposta_id: int, session: SessionDep, usuario: LeadsManageDep) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    token = secrets.token_urlsafe(40)
    proposta.public_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    proposta.public_token_expira_em = datetime.now(UTC) + timedelta(days=7)
    await session.commit()
    base = get_settings().app_public_url.rstrip("/")
    return {"link": f"{base}/propostas/{token}", "expira_em": proposta.public_token_expira_em}


@router.post("/v1/admin/propostas/{proposta_id}/enviar")
async def enviar_link_proposta(proposta_id: int, session: SessionDep, usuario: LeadsManageDep) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == proposta.lead_id, Lead.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if lead is None or not lead.email:
        raise HTTPException(status_code=422, detail="O lead não possui e-mail cadastrado")
    token = secrets.token_urlsafe(40)
    proposta.public_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    proposta.public_token_expira_em = datetime.now(UTC) + timedelta(days=7)
    proposta.status = "enviada"
    proposta.enviado_em = datetime.now(UTC)
    await session.commit()
    org = await session.get(Organizacao, usuario.organizacao_id)
    link = f"{get_settings().app_public_url.rstrip('/')}/propostas/{token}"
    pdf = gerar_pdf_proposta(_proposta_dict(proposta, org))
    clicksign = configuracao_clicksign(org)
    if clicksign["enabled"]:
        try:
            ids = await criar_envelope(pdf, f"Proposta {proposta.numero}", lead.email, lead.nome, org)
            proposta.dados = {**(proposta.dados or {}), "clicksign": ids}
            await session.commit()
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Não foi possível enviar à Clicksign: {type(exc).__name__}"
            ) from exc
    await enviar_proposta_email(lead.email, lead.nome, link, pdf, proposta.numero)
    return {
        "link": link,
        "destinatario": lead.email,
        "expira_em": proposta.public_token_expira_em,
        "clicksign": proposta.dados.get("clicksign") if proposta.dados else None,
    }


@router.get("/propostas/{token}", response_class=HTMLResponse, include_in_schema=False)
async def visualizar_proposta_publica(token: str, session: SessionDep) -> HTMLResponse:
    proposta = await _proposta_por_token(session, token)
    if proposta is None:
        return HTMLResponse(
            "<h1>Link expirado</h1><p>Solicite uma nova proposta ao atendimento.</p>",
            status_code=404,
        )
    org = await session.get(Organizacao, proposta.organizacao_id)

    def safe(value: object) -> str:
        return html.escape(str(value or ""))

    def moeda(valor: object) -> str:
        return f"R$ {float(valor or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    if proposta.status == "enviada":
        proposta.status = "visualizada"
        await session.commit()
    total = (proposta.honorarios or 0) + (proposta.taxa_gru or 0)
    # Achado 5 do plano proposta-financeiro (Fase 1, 03/09/2026): antes o link
    # público só mostrava marca/classes/escopo -- o cliente aceitava sem ver
    # valores, condições de pagamento ou validade da proposta.
    validade_html = (
        f"<p class='muted'>Proposta válida até {proposta.validade_em.strftime('%d/%m/%Y')}.</p>"
        if proposta.validade_em
        else ""
    )
    return HTMLResponse(
        f"""<!doctype html><html lang='pt-BR'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Proposta {safe(proposta.numero)} - {safe(org.nome)}</title><style>body{{font:16px Arial;color:#17231c;background:#f5f7f5;margin:0;padding:24px}}main{{max-width:760px;margin:auto;background:white;padding:36px;border-radius:18px;border:1px solid #d8ddd6}}h1{{font-family:Georgia,serif}}.muted{{color:#5b665f}}.button{{display:inline-block;background:#086044;color:#fff;padding:13px 20px;border-radius:9px;text-decoration:none;border:0;font-weight:700;cursor:pointer}}</style>
        <main><p class='muted'>{safe(org.nome)}</p><h1>Proposta de registro de marca</h1><p>Proposta <strong>{safe(proposta.numero)}</strong> · versão {proposta.versao}</p>
        <h2>Marca</h2><p>{safe(proposta.marca or "A definir")} · Classes {safe(proposta.classes or "A definir")}</p><h2>Escopo</h2><p>{safe(proposta.escopo)}</p>
        <h2>Valores</h2><p>Honorários: {safe(moeda(proposta.honorarios))}<br>Taxa GRU: {safe(moeda(proposta.taxa_gru))}<br><strong>Total: {safe(moeda(total))}</strong></p>
        <h2>Condições de pagamento</h2><p>{safe(proposta.condicoes_pagamento or "A combinar com o atendimento")}</p>
        {validade_html}
        <p class='muted'>Após aceite, pagamento e documentação completa, o protocolo será realizado em até 24 horas úteis. O protocolo não garante a concessão da marca.</p>
        <form method='post' action='/propostas/{token}/aceitar'><button class='button' type='submit'>Aceitar proposta</button></form></main></html>"""
    )


@router.post("/propostas/{token}/aceitar", response_class=HTMLResponse, include_in_schema=False)
async def aceitar_proposta_publica(token: str, request: Request, session: SessionDep) -> HTMLResponse:
    proposta = await _proposta_por_token(session, token)
    if proposta is None:
        return HTMLResponse("<h1>Link expirado</h1>", status_code=404)
    if proposta.status not in ("enviada", "visualizada", "aceita"):
        return HTMLResponse(
            "<h1>Proposta indisponível</h1><p>Solicite uma nova versão ao atendimento.</p>",
            status_code=409,
        )
    # Achado 7 do plano proposta-financeiro (Fase 1): validade_em nunca era
    # checada -- só o token de 7 dias. Uma proposta já aceita continua
    # idempotente mesmo depois de vencer (não desfaz um aceite já registrado).
    if proposta.public_aceito_em is None and proposta.validade_em and proposta.validade_em < datetime.now(UTC).date():
        return HTMLResponse(
            "<h1>Proposta expirada</h1><p>Esta proposta não está mais disponível para aceite. "
            "Solicite uma nova versão ao atendimento.</p>",
            status_code=409,
        )
    if proposta.public_aceito_em is None:
        proposta.public_aceito_em = datetime.now(UTC)
        proposta.aceito_em = proposta.public_aceito_em
        proposta.public_aceito_ip_hash = hash_ip(cliente_ip(request))
        proposta.status = "aceita"
        proposta.sla_status = "aguardando_pagamento"
        assinatura_hash = hashlib.sha256(
            "|".join(
                str(valor or "")
                for valor in (
                    proposta.numero,
                    proposta.versao,
                    proposta.marca,
                    proposta.classes,
                    proposta.escopo,
                    proposta.honorarios,
                    proposta.taxa_gru,
                    proposta.condicoes_pagamento,
                )
            ).encode("utf-8")
        ).hexdigest()
        session.add(
            AssinaturaPropostaComercial(
                organizacao_id=proposta.organizacao_id,
                proposta_id=proposta.id,
                versao=proposta.versao,
                hash_documento=assinatura_hash,
                ip_hash=proposta.public_aceito_ip_hash,
                provedor="link_publico",
            )
        )
        session.add(
            EventoAuditoria(
                organizacao_id=proposta.organizacao_id,
                ator="cliente_link",
                acao="aceitar_proposta",
                recurso=f"proposta:{proposta.id}",
                resource_type="proposta",
                resource_id=str(proposta.id),
                sucesso=True,
                status_http=200,
                ip_hash=proposta.public_aceito_ip_hash,
                detalhes={"origem": "link_publico", "proposta": proposta.numero},
            )
        )
        registrar_evento_operacional(
            session,
            organizacao_id=proposta.organizacao_id,
            dominio="crm",
            tipo="crm.proposta_aceita",
            entidade_tipo="lead",
            entidade_id=proposta.lead_id,
            ator="cliente_link",
            payload={"proposta_id": proposta.id, "aceito_em": proposta.aceito_em.isoformat()},
        )
        lead = (
            await session.execute(
                select(Lead).where(Lead.id == proposta.lead_id, Lead.organizacao_id == proposta.organizacao_id)
            )
        ).scalar_one_or_none()
        if lead is not None:
            await avancar_fase_lead(session, lead, "proposta_aceita", "Cliente via link")
        await session.commit()
    return HTMLResponse(
        "<h1>Proposta aceita</h1><p>Recebemos seu aceite. Nossa equipe dará continuidade ao atendimento.</p>"
    )


# --- Guias do INPI (GRU) por lead -----------------------------------------


class GuiaInpiInput(BaseModel):
    descricao: str = Field(min_length=2, max_length=200)
    servico: str | None = Field(default=None, max_length=60)
    codigo: str | None = Field(default=None, max_length=10)
    valor: Decimal | None = Field(default=None, ge=0)
    reduzido: bool = False
    numero_gru: str | None = Field(default=None, max_length=60)
    vencimento: date | None = None
    observacoes: str | None = Field(default=None, max_length=2000)


class GuiaStatusInput(BaseModel):
    status: Literal["pendente", "paga", "cancelada"]
    pago_em: date | None = None


def _guia_dict(g: GuiaInpi) -> dict:
    return {
        "id": g.id,
        "servico": g.servico,
        "codigo": g.codigo,
        "descricao": g.descricao,
        "valor": g.valor,
        "reduzido": g.reduzido,
        "numero_gru": g.numero_gru,
        "vencimento": g.vencimento,
        "status": g.status,
        "pago_em": g.pago_em,
        "observacoes": g.observacoes,
        "criado_em": g.criado_em,
    }


@router.get("/v1/admin/leads/{lead_id}/guias")
async def listar_guias_inpi(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    guias = (
        (
            await session.execute(
                select(GuiaInpi)
                .where(
                    GuiaInpi.lead_id == lead_id,
                    GuiaInpi.organizacao_id == usuario.organizacao_id,
                )
                .order_by(GuiaInpi.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    ja = {g.servico for g in guias if g.servico}
    sugeridas = (
        (
            await session.execute(
                select(RetribuicaoInpi)
                .where(
                    RetribuicaoInpi.fase_sugerida == lead.fase,
                    RetribuicaoInpi.ativo.is_(True),
                )
                .order_by(RetribuicaoInpi.ordem, RetribuicaoInpi.descricao)
            )
        )
        .scalars()
        .all()
    )
    return {
        "fase": lead.fase,
        "guias": [_guia_dict(g) for g in guias],
        "sugestoes": [
            {
                "servico": s.servico,
                "codigo": s.codigo,
                "descricao": s.descricao,
                "valor_normal": s.valor_normal,
                "valor_reduzido": s.valor_reduzido,
                "ja_registrada": s.servico in ja,
            }
            for s in sugeridas
        ],
    }


@router.post("/v1/admin/leads/{lead_id}/guias", status_code=status.HTTP_201_CREATED)
async def criar_guia_inpi(lead_id: int, dados: GuiaInpiInput, session: SessionDep, usuario: LeadsManageDep) -> dict:
    await _lead_da_org(session, lead_id, usuario.organizacao_id)
    guia = GuiaInpi(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead_id,
        servico=dados.servico or None,
        codigo=dados.codigo or None,
        descricao=dados.descricao.strip(),
        valor=dados.valor,
        reduzido=dados.reduzido,
        numero_gru=dados.numero_gru or None,
        vencimento=dados.vencimento,
        observacoes=dados.observacoes,
    )
    session.add(guia)
    await session.commit()
    return {"id": guia.id}


@router.patch("/v1/admin/guias-inpi/{guia_id}")
async def atualizar_guia_inpi(
    guia_id: int, dados: GuiaStatusInput, session: SessionDep, usuario: LeadsManageDep
) -> dict:
    guia = (
        await session.execute(
            select(GuiaInpi).where(GuiaInpi.id == guia_id, GuiaInpi.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if guia is None:
        raise HTTPException(status_code=404, detail="Guia não encontrada")
    guia.status = dados.status
    guia.pago_em = (dados.pago_em or datetime.now(UTC).date()) if dados.status == "paga" else None
    await session.commit()
    return {"ok": True}


@router.delete("/v1/admin/guias-inpi/{guia_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_guia_inpi(guia_id: int, session: SessionDep, usuario: LeadsManageDep) -> Response:
    guia = (
        await session.execute(
            select(GuiaInpi).where(GuiaInpi.id == guia_id, GuiaInpi.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if guia is None:
        raise HTTPException(status_code=404, detail="Guia não encontrada")
    await session.delete(guia)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class AplicarCadenciaInput(BaseModel):
    cadencia_id: int


@router.post("/v1/admin/leads/{lead_id}/aplicar-cadencia")
async def aplicar_cadencia_lead(
    lead_id: int, dados: AplicarCadenciaInput, session: SessionDep, usuario: LeadsManageDep
) -> dict:
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
        raise HTTPException(status_code=404, detail="Oportunidade não encontrada")
    cadencia = (
        await session.execute(
            select(Cadencia)
            .where(
                Cadencia.id == dados.cadencia_id,
                Cadencia.organizacao_id == usuario.organizacao_id,
            )
            .options(selectinload(Cadencia.passos))
        )
    ).scalar_one_or_none()
    if cadencia is None:
        raise HTTPException(status_code=404, detail="Cadência não encontrada")
    agora = datetime.now(UTC)
    criados = 0
    for passo in cadencia.passos:
        chave_idempotencia = f"cadencia:{lead.id}:{cadencia.id}:{passo.id}"
        descricao = f"Cadência “{cadencia.nome}” · canal {passo.canal}"
        if passo.descricao:
            descricao += f" — {passo.descricao}"
        # INSERT com ON CONFLICT DO NOTHING em vez de SELECT-em-lote-depois-INSERT:
        # duas chamadas quase simultaneas (duplo clique em "aplicar cadência")
        # podiam ambas passar pelo SELECT antes de comitar e colidir na
        # constraint unica so no commit final, virando 500 nao tratado.
        inserido = (
            await session.execute(
                pg_insert(LembreteCRM)
                .values(
                    organizacao_id=usuario.organizacao_id,
                    lead_id=lead.id,
                    responsavel_id=lead.responsavel_id,
                    tipo="retorno",
                    prioridade="media",
                    titulo=passo.titulo,
                    descricao=descricao,
                    lembrar_em=agora + timedelta(days=passo.dia),
                    status="pendente",
                    criado_por=f"Cadência ({usuario.nome})"[:254],
                    criado_por_id=usuario.id,
                    idempotency_key=chave_idempotencia,
                )
                .on_conflict_do_nothing(constraint="uq_lembrete_crm_idempotencia")
                .returning(LembreteCRM.id)
            )
        ).scalar_one_or_none()
        if inserido is None:
            continue
        criados += 1
        registrar_evento_operacional(
            session,
            organizacao_id=usuario.organizacao_id,
            dominio="crm",
            tipo="cadencia.tarefa_criada",
            entidade_tipo="lead",
            entidade_id=lead.id,
            ator=usuario.nome or "sistema",
            ator_id=usuario.id,
            payload={"cadencia_id": cadencia.id, "passo_id": passo.id},
            idempotency_key=chave_idempotencia,
        )
    await session.commit()
    return {"criados": criados, "ignorados_idempotentes": len(cadencia.passos) - criados}


FASE_LABELS: dict[str, str] = {
    "contato_inicial": "Contato inicial",
    "relatorio_enviado": "Relatório enviado",
    "proposta_enviada": "Proposta enviada",
    "proposta_aceita": "Proposta aceita",
    "pagamento_realizado": "Pagamento",
    "protocolo_inpi": "Protocolo INPI",
    "processo_inpi": "Processo no INPI",
}


def _para_dt(valor) -> datetime:
    """Normaliza date/datetime para datetime aware (UTC) para ordenação."""
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    return datetime.combine(valor, datetime.min.time(), tzinfo=UTC)


@router.get("/v1/admin/leads/{lead_id}/timeline")
async def timeline_lead(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    org = usuario.organizacao_id
    eventos: list[dict] = [
        {
            "tipo": "criado",
            "data": lead.criado_em,
            "titulo": "Oportunidade criada",
            "detalhe": f"Origem: {lead.origem}",
        }
    ]
    fases = (
        (
            await session.execute(
                select(HistoricoFaseLead).where(
                    HistoricoFaseLead.lead_id == lead_id, HistoricoFaseLead.organizacao_id == org
                )
            )
        )
        .scalars()
        .all()
    )
    for f in fases:
        eventos.append(
            {
                "tipo": "fase",
                "data": f.entrou_em,
                "titulo": f"Fase: {FASE_LABELS.get(f.fase, f.fase)}",
                "detalhe": f"por {f.por}" if f.por else None,
            }
        )
    contatos = (
        (
            await session.execute(
                select(ContatoLead).where(ContatoLead.lead_id == lead_id, ContatoLead.organizacao_id == org)
            )
        )
        .scalars()
        .all()
    )
    for c in contatos:
        detalhe = " · ".join(x for x in (c.resultado, c.observacao) if x) or None
        eventos.append(
            {
                "tipo": "contato",
                "data": c.criado_em,
                "titulo": f"Contato · {c.canal}",
                "detalhe": detalhe,
                "autor": c.operador_nome,
            }
        )
    pesquisas = (
        await session.execute(
            select(PesquisaMarca.criado_em, PesquisaMarca.marca).where(
                PesquisaMarca.lead_id == lead_id, PesquisaMarca.organizacao_id == org
            )
        )
    ).all()
    for criado_em, marca in pesquisas:
        eventos.append({"tipo": "pesquisa", "data": criado_em, "titulo": "Pesquisa gerada", "detalhe": marca})
    propostas = (
        (
            await session.execute(
                select(PropostaComercial).where(
                    PropostaComercial.lead_id == lead_id,
                    PropostaComercial.organizacao_id == org,
                )
            )
        )
        .scalars()
        .all()
    )
    for proposta in propostas:
        eventos.append(
            {
                "tipo": "proposta",
                "data": proposta.enviado_em or proposta.criado_em,
                "titulo": f"Proposta {proposta.numero}",
                "detalhe": f"Status: {proposta.status}",
            }
        )
    mensagens_portal = (
        (
            await session.execute(
                select(MensagemClientePortal).where(
                    MensagemClientePortal.lead_id == lead_id,
                    MensagemClientePortal.organizacao_id == org,
                )
            )
        )
        .scalars()
        .all()
    )
    for mensagem in mensagens_portal:
        eventos.append(
            {
                "tipo": "mensagem_portal",
                "data": mensagem.criado_em,
                "titulo": "Mensagem do cliente" if mensagem.autor_tipo == "cliente" else "Resposta do atendimento",
                "detalhe": mensagem.mensagem,
            }
        )
    eventos_dominio = (
        (
            await session.execute(
                select(EventoDominio).where(
                    EventoDominio.organizacao_id == org,
                    EventoDominio.entidade_tipo == "lead",
                    EventoDominio.entidade_id == str(lead_id),
                )
            )
        )
        .scalars()
        .all()
    )
    for evento in eventos_dominio:
        eventos.append(
            {
                "tipo": evento.tipo,
                "data": evento.ocorrido_em,
                "titulo": evento.tipo.replace(".", " · "),
                "detalhe": evento.payload.get("descricao") or evento.payload.get("regra"),
                "autor": evento.ator,
            }
        )
    documentos = (
        (
            await session.execute(
                select(DocumentoLead).where(DocumentoLead.lead_id == lead_id, DocumentoLead.organizacao_id == org)
            )
        )
        .scalars()
        .all()
    )
    for d in documentos:
        if not (d.numero or d.data):
            continue
        eventos.append(
            {
                "tipo": "documento",
                "data": d.atualizado_em,
                "titulo": f"Documento · {d.tipo}",
                "detalhe": " · ".join(x for x in (d.numero, d.status) if x) or None,
            }
        )
    guias = (
        (await session.execute(select(GuiaInpi).where(GuiaInpi.lead_id == lead_id, GuiaInpi.organizacao_id == org)))
        .scalars()
        .all()
    )
    for g in guias:
        eventos.append(
            {
                "tipo": "guia",
                "data": g.criado_em,
                "titulo": "GRU registrada",
                "detalhe": g.descricao,
            }
        )
        if g.pago_em:
            eventos.append(
                {
                    "tipo": "guia_paga",
                    "data": g.pago_em,
                    "titulo": "GRU paga",
                    "detalhe": g.descricao,
                }
            )
    if lead.resultado:
        eventos.append(
            {
                "tipo": lead.resultado,
                "data": lead.atualizado_em,
                "titulo": "Convertido (ganho)" if lead.resultado == "ganho" else "Perdido",
                "detalhe": MOTIVOS_PERDA.get(lead.motivo_perda or "", lead.motivo_perda),
            }
        )
    eventos.sort(key=lambda e: _para_dt(e["data"]), reverse=True)
    for e in eventos:
        e["data"] = _para_dt(e["data"]).isoformat()
        e["dominio"] = "crm"
        e["versao"] = 1
        e["entidade"] = {"tipo": "lead", "id": str(lead.id)}
        e["payload"] = {
            "titulo": e.get("titulo"),
            "detalhe": e.get("detalhe"),
            "autor": e.get("autor"),
        }
    return {"schema": "timeline.operacional.v1", "eventos": eventos}


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
    prioridade: PrioridadeLead = None,
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
        prioridade,
    )
    leads = (
        (await session.execute(select(Lead).where(*filtros).order_by(Lead.criado_em.desc()).limit(5000)))
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

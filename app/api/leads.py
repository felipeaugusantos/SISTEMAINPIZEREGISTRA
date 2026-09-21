import csv
import hashlib
import io
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.email_leads_config import config_email_leads
from app.api.juridico import FUSO_BRASIL
from app.auth import AcaoAdminDep, UsuarioAutenticado, exigir_permissao, hash_ip
from app.cadencia_email import processar_descadastro_cadencia, registrar_abertura
from app.crm import (
    DOMINIO_CLIENTE_SEM_EMAIL,
    aplicar_cadencia_a_lead,
    aplicar_cadencias_automaticas,
    aplicar_politica_oportunidade,
    aplicar_regras_automacao,
    avancar_fase_lead,
    buscar_lead_ativo_por_email,
    calcular_score_lead,
    distribuir_lead_automaticamente,
    email_sintetico_por_telefone,
    obter_ou_criar_empresa,
    obter_politica_crm,
    registrar_consentimento_operador,
    registrar_consentimento_titular,
    registrar_evento_operacional,
    sincronizar_fase_por_status,
)
from app.database import get_session
from app.emailing import (
    enviar_alerta_lead_atribuido,
    enviar_alerta_novo_lead,
    enviar_email_prospeccao_lead,
)
from app.ia_sombra import enfileirar_qualificacao_ia_se_ativa
from app.importacao_planilha import TAMANHO_MAXIMO_IMPORTACAO, ler_planilha, valor_coluna
from app.malware_scan import escanear_upload_ou_rejeitar
from app.models import (
    MOTIVOS_PERDA,
    ORDEM_FASE_LEAD,
    TIPOS_DOCUMENTO_LEAD,
    AvaliacaoRiscoMarca,
    Cadencia,
    CadenciaPasso,
    CanalContato,
    ChecklistFaseLead,
    Contato,
    ContatoLead,
    ContratacaoServico,
    DocumentoLead,
    EmpresaCRM,
    EnvioCadenciaEmail,
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
    QualificacaoIALead,
    RespostaEmailLead,
    SolicitacaoExclusaoPesquisa,
    StatusLead,
    SugestaoIALead,
    TipoProcesso,
    UsuarioOperacoes,
    VersaoDocumentoLead,
    VersaoRelatorioMarca,
)
from app.normalization import normalizar_numero_processo
from app.proxy import cliente_ip
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
from app.storage import StorageError, local_root, read_bytes, save_bytes
from app.tenancy import OrganizacaoPublicaDep
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

# Achado CRM-11 da auditoria (04/09/2026): funil expandido de 7 para 10
# fases -- qualificado ganhou posição própria, pagamento virou duas etapas
# (aguardando/confirmado) e "ganho" passou a ser uma fase do funil, não só
# um resultado à parte. Ver FaseLead em app/models.py para a decisão sobre
# não existir "contrato_assinado" separado de "proposta_aceita".
KANBAN_ETAPAS = {
    "primeiro_contato": {"label": "Primeiro contato", "fase": "contato_inicial"},
    "aguardando_contato_nosso": {"label": "Aguardando contato nosso", "fase": "contato_inicial"},
    "qualificado": {"label": "Qualificado", "fase": "qualificado"},
    "aguardando_retorno_cliente": {
        "label": "Aguardando retorno do cliente",
        "fase": "relatorio_enviado",
    },
    "proposta_enviada": {"label": "Proposta enviada", "fase": "proposta_enviada"},
    "proposta_aceita": {"label": "Proposta aceita", "fase": "proposta_aceita"},
    "aguardando_pagamento": {"label": "Aguardando pagamento", "fase": "aguardando_pagamento"},
    "pagamento_confirmado": {"label": "Pagamento confirmado", "fase": "pagamento_confirmado"},
    "ganho": {"label": "Ganho", "fase": "ganho"},
    "protocolo_inpi": {"label": "Protocolo no INPI gerado", "fase": "protocolo_inpi"},
    "processo_inpi": {"label": "Processo no INPI", "fase": "processo_inpi"},
    # Achado H3/P1 da auditoria (10/09/2026): lead descartado não tinha coluna
    # própria e ficava preso na última coluna ativa antes do descarte,
    # contado junto com oportunidades abertas. "fase" aqui não corresponde a
    # nenhum valor de FaseLead (não avança o funil) -- é só o rótulo da
    # coluna; a movimentação real acontece via PATCH status=descartado (que
    # já exige motivo_perda), nunca arrastando o card, ver mover_lead_kanban.
    "perdidos": {"label": "Perdidos", "fase": "perdidos"},
}

# Desenho do setor de Atendimento & Comercial (10/09/2026): prazo máximo sem
# mexida no card antes de virar alerta de SLA no kanban. Etapas sem prazo
# definido (acompanhamento de longo prazo, sem urgência de resposta) ficam
# de fora do dict e nunca disparam alerta.
SLA_HORAS_POR_ETAPA: dict[str, int] = {
    "primeiro_contato": 4,
    "aguardando_contato_nosso": 4,
    "qualificado": 24,
    "aguardando_retorno_cliente": 72,
    "proposta_enviada": 48,
    "proposta_aceita": 24,
    "aguardando_pagamento": 48,
    "pagamento_confirmado": 24,
    "protocolo_inpi": 72,
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
    # Achado REG-1 da auditoria Fase 1 (04/09/2026): investigado -- a flag fixada
    # em True não é um bug isolado. O PDF preliminar já é rotulado com honestidade
    # (X-Relatorio-Status: preliminar, auditoria registra "preliminar" vs
    # "validado" -- ver abaixo) e é um recurso deliberado e testado
    # (test_relatorio_preliminar_e_permitido_enquanto_revisao_esta_pendente).
    # O risco real (conteúdo apresentado como favorável sem fundamento
    # suficiente) é resolvido no conteúdo, não bloqueando a emissão: ver
    # RelatorioMarcaResponse.veredito_publico (app/trademarks/veredito.py),
    # que nunca retorna FAVORAVEL sem busca concluída, base identificada,
    # ausência de impedimento e modelo ACTIVE com probabilidade suficiente --
    # vale tanto para relatório preliminar quanto validado. Mantida
    # deliberadamente sem uso de config (é sempre True hoje; se um dia a
    # política mudar para permitir desligar reports preliminares, essa é a
    # variável a tornar configurável).
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
    logo_asset = lead.logo_cliente or {}
    dados.logo_cliente_url = f"/v1/admin/leads/{lead.id}/logo-cliente" if logo_asset.get("sha256") else None
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
) -> LeadResponse:
    if dados.website:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Envio inválido")

    # Achado P0 da auditoria de Leads (03/09/2026): a comparação exata de string
    # não reconhecia o mesmo telefone em máscaras diferentes -- (16) 99999-9999
    # e 16999999999 viravam leads duplicados. Compara só os dígitos dos dois lados.
    telefone_digitos = "".join(caractere for caractere in dados.telefone if caractere.isdigit())
    consulta_existente = (
        select(Lead)
        .where(
            Lead.organizacao_id == organizacao.id,
            Lead.arquivado_em.is_(None),
            or_(
                func.lower(Lead.email) == dados.email.lower(),
                func.regexp_replace(Lead.telefone, r"\D", "", "g") == telefone_digitos,
            ),
        )
        .order_by(
            case((func.lower(Lead.email) == dados.email.lower(), 0), else_=1),
            Lead.atualizado_em.desc(),
        )
        .limit(1)
    )
    existente = (await session.execute(consulta_existente)).scalar_one_or_none()
    marca_nova = dados.marca.strip()
    # Achado L1/L2/L3 do plano Leads/CRM: a mesma pessoa interessada numa marca
    # diferente da já registrada vira uma nova oportunidade, em vez de sobrescrever
    # (e perder) a marca/origem do lead anterior. Reenvio sem marca ou com a mesma
    # marca continua atualizando no lugar (comportamento de sempre).
    mesma_oportunidade = existente is not None and (
        not marca_nova or existente.marca.strip().lower() == marca_nova.lower()
    )
    if mesma_oportunidade:
        # Achado H7/P1 da auditoria (10/09/2026): reenviar o formulário
        # público para uma oportunidade já CONVERTIDA (negócio fechado)
        # sobrescrevia nome/e-mail/telefone do registro fechado. Só registra
        # que o cliente voltou a se manifestar, sem mutar o negócio já
        # ganho -- quem responde a esse novo interesse abre uma oportunidade
        # nova manualmente, se for o caso.
        if existente.status == StatusLead.CONVERTIDO:
            registrar_evento_operacional(
                session,
                organizacao_id=existente.organizacao_id,
                dominio="crm",
                tipo="crm.lead_reenvio_ignorado",
                entidade_tipo="lead",
                entidade_id=existente.id,
                ator="formulario_publico",
                payload={"motivo": "oportunidade_ja_convertida", "email": dados.email.lower()},
            )
            await session.commit()
            await session.refresh(existente)
            resposta = LeadResponse.model_validate(existente)
            resposta.documento = _mascarar_documento(resposta.documento)
            return resposta
        # Achado H7/P1: oportunidade DESCARTADA que reaparece pelo formulário
        # público é reaberta (o cliente voltou a manifestar interesse) em vez
        # de continuar descartada para sempre com os dados silenciosamente
        # sobrescritos por baixo.
        reabrindo_descartado = existente.status == StatusLead.DESCARTADO
        existente.nome = dados.nome
        existente.email = dados.email.lower()
        existente.telefone = dados.telefone
        existente.marca = dados.marca or existente.marca
        existente.processo_numero = dados.processo_numero or existente.processo_numero
        # Achado CRM-5 da auditoria (04/09/2026): a origem original de um lead
        # nao pode ser sobrescrita quando ele reaparece (ex.: reenvio do
        # formulario por outro canal) -- e a mesma logica ja aplicada acima
        # para marca/processo_numero. A origem do NOVO touch, se precisar
        # ficar visivel, mora no last-touch (utm_*_ultimo abaixo).
        existente.tipo_interesse = dados.tipo_interesse or existente.tipo_interesse
        existente.utm_source_ultimo = dados.utm_source or existente.utm_source_ultimo
        existente.utm_medium_ultimo = dados.utm_medium or existente.utm_medium_ultimo
        existente.utm_campaign_ultimo = dados.utm_campaign or existente.utm_campaign_ultimo
        registrar_consentimento_titular(existente, organizacao.politica_privacidade_versao)
        if reabrindo_descartado:
            existente.status = StatusLead.NOVO
            existente.resultado = None
            existente.motivo_perda = None
            existente.motivo_perda_detalhe = None
            registrar_evento_operacional(
                session,
                organizacao_id=existente.organizacao_id,
                dominio="crm",
                tipo="crm.lead_reaberto",
                entidade_tipo="lead",
                entidade_id=existente.id,
                ator="formulario_publico",
                payload={"motivo": "reenvio_formulario_publico"},
            )
        if existente.status not in (StatusLead.CONVERTIDO, StatusLead.DESCARTADO):
            await _garantir_proxima_acao_padrao(session, existente)
        await session.commit()
        await session.refresh(existente)
        resposta = LeadResponse.model_validate(existente)
        resposta.documento = _mascarar_documento(resposta.documento)
        return resposta

    lead = Lead(
        organizacao_id=organizacao.id,
        nome=dados.nome,
        email=dados.email.lower(),
        telefone=dados.telefone,
        documento=existente.documento if existente is not None else None,
        empresa=existente.empresa if existente is not None else None,
        marca=dados.marca,
        processo_numero=dados.processo_numero,
        origem=dados.origem,
        tipo_interesse=dados.tipo_interesse,
        aceite_privacidade=True,
        status=StatusLead.NOVO,
        utm_source=dados.utm_source,
        utm_medium=dados.utm_medium,
        utm_campaign=dados.utm_campaign,
        utm_source_ultimo=dados.utm_source,
        utm_medium_ultimo=dados.utm_medium,
        utm_campaign_ultimo=dados.utm_campaign,
    )
    registrar_consentimento_titular(lead, organizacao.politica_privacidade_versao)
    session.add(lead)
    await _garantir_proxima_acao_padrao(session, lead)
    await session.commit()
    await session.refresh(lead)
    if lead.responsavel_id is None:
        # Achado P0 da auditoria de Leads: nenhum alerta ativo avisava a equipe
        # de um lead novo chegando pelo formulário genérico de captação.
        await enviar_alerta_novo_lead(lead.nome, lead.email, lead.telefone, lead.marca, lead.origem)
    await enfileirar_qualificacao_ia_se_ativa(session, lead)
    resposta = LeadResponse.model_validate(lead)
    resposta.documento = _mascarar_documento(resposta.documento)
    return resposta


COLUNAS_NOME_LEAD = ("nome", "cliente", "contato")
COLUNAS_EMAIL_LEAD = ("email",)
COLUNAS_TELEFONE_LEAD = ("telefone", "fone", "celular", "whatsapp")
COLUNAS_MARCA_LEAD = ("marca",)
COLUNAS_EMPRESA_LEAD = ("empresa", "razaosocial")
COLUNAS_OBS_LEAD = ("observ", "obs", "notas")


@router.post("/v1/admin/leads/importar", status_code=status.HTTP_201_CREATED)
async def importar_leads(
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
    arquivo: Annotated[UploadFile, File()],
) -> dict:
    """Importa leads em lote (CSV/XLSX) -- achado Fase 4 do roadmap pós-auditoria
    de Leads (03/09/2026): captação hoje só entra pelo formulário público ou uma
    a uma pelo atendente, sem forma de trazer uma carteira externa de prospecção.

    Colunas reconhecidas (cabeçalho, sem acento/maiúsculas): nome (obrigatória),
    email e/ou telefone (ao menos um obrigatório), marca, empresa, observacoes.
    Linhas cujo e-mail ou telefone já pertence a um lead ativo são ignoradas
    (mesma regra de deduplicação do formulário público).
    """
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(400, "Arquivo vazio.")
    if len(conteudo) > TAMANHO_MAXIMO_IMPORTACAO:
        raise HTTPException(413, "Arquivo muito grande (máximo 5 MB).")
    registros = ler_planilha(conteudo, arquivo.filename or "")
    if not registros:
        raise HTTPException(400, "Planilha vazia ou sem cabeçalho reconhecível. Inclua uma coluna 'nome'.")

    linhas_validas: list[dict[str, str | None]] = []
    sem_dados_essenciais = 0
    for registro in registros:
        nome = valor_coluna(registro, COLUNAS_NOME_LEAD)
        email = valor_coluna(registro, COLUNAS_EMAIL_LEAD)
        telefone = valor_coluna(registro, COLUNAS_TELEFONE_LEAD)
        if not nome or not (email or telefone):
            sem_dados_essenciais += 1
            continue
        linhas_validas.append(
            {
                "nome": nome,
                "email": (email or "").lower(),
                "telefone": telefone or "",
                "marca": valor_coluna(registro, COLUNAS_MARCA_LEAD) or "",
                "empresa": valor_coluna(registro, COLUNAS_EMPRESA_LEAD),
                "observacoes": valor_coluna(registro, COLUNAS_OBS_LEAD),
            }
        )
    if not linhas_validas:
        colunas = ", ".join(chave for chave in registros[0] if chave) or "nenhuma"
        raise HTTPException(
            400,
            "Nenhuma linha com nome e (e-mail ou telefone) foi reconhecida. "
            f"Colunas detectadas no arquivo: {colunas}.",
        )

    emails = {linha["email"] for linha in linhas_validas if linha["email"]}
    telefones_digitos = {
        "".join(c for c in str(linha["telefone"]) if c.isdigit()) for linha in linhas_validas if linha["telefone"]
    }
    existentes = (
        await session.execute(
            select(Lead.email, Lead.telefone).where(
                Lead.organizacao_id == usuario.organizacao_id,
                Lead.arquivado_em.is_(None),
                or_(
                    func.lower(Lead.email).in_(emails),
                    func.regexp_replace(Lead.telefone, r"\D", "", "g").in_(telefones_digitos),
                ),
            )
        )
    ).all()
    emails_vistos = {email.lower() for email, _ in existentes if email}
    telefones_vistos = {"".join(c for c in telefone if c.isdigit()) for _, telefone in existentes if telefone}

    politica = await obter_politica_crm(session, usuario.organizacao_id)
    proxima_acao_padrao = (
        datetime.now(UTC) + timedelta(days=politica.dias_proxima_acao_padrao)
        if politica.dias_proxima_acao_padrao is not None
        else None
    )

    criados = 0
    duplicados = 0
    for linha in linhas_validas:
        email = str(linha["email"])
        telefone_digitos = "".join(c for c in str(linha["telefone"]) if c.isdigit())
        if (email and email in emails_vistos) or (telefone_digitos and telefone_digitos in telefones_vistos):
            duplicados += 1
            continue
        lead = Lead(
            organizacao_id=usuario.organizacao_id,
            nome=str(linha["nome"]),
            email=email,
            telefone=str(linha["telefone"]),
            empresa=linha["empresa"],
            marca=str(linha["marca"]),
            origem="importacao",
            aceite_privacidade=True,
            status=StatusLead.NOVO,
            notas=linha["observacoes"],
            proxima_acao_em=proxima_acao_padrao,
        )
        registrar_consentimento_operador(lead, usuario.id)
        session.add(lead)
        criados += 1
        if email:
            emails_vistos.add(email)
        if telefone_digitos:
            telefones_vistos.add(telefone_digitos)

    resultado = {
        "total_linhas": len(registros),
        "criados": criados,
        "duplicados": duplicados,
        "sem_dados_essenciais": sem_dados_essenciais,
    }
    _auditar(
        session,
        usuario,
        request,
        "importar_leads",
        f"leads:importacao:{arquivo.filename}",
        resultado,
    )
    await session.commit()
    return resultado


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
    # Achado do usuário (16/09/2026, item 2): distribuição automática é
    # opt-in silencioso -- sem isto, uma organização nova nunca via que a
    # opção existia nem que estava desligada, mesmo acumulando leads "sem
    # responsável" no card ao lado. Devolve o estado para o frontend decidir
    # quando mostrar o aviso (só importa se ainda restam leads sem dono).
    politica = await obter_politica_crm(session, usuario.organizacao_id)
    return {
        "sem_responsavel": int(linha[0] or 0),
        "atrasadas": int(linha[1] or 0),
        "sem_proxima_acao": int(linha[2] or 0),
        "distribuicao_automatica_ativa": politica.distribuicao_automatica_ativa,
        "atualizado_em": agora,
    }


def _kanban_etapa(lead: Lead) -> str:
    # Descartado sempre cai em "Perdidos", independentemente da fase em que
    # estava -- checagem primeiro para não ser mascarada pelas regras de fase
    # abaixo (achado H3/P1 da auditoria, 10/09/2026).
    if lead.status == StatusLead.DESCARTADO:
        return "perdidos"
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
    agora = datetime.now(UTC)
    cards = []
    for lead in leads:
        etapa = _kanban_etapa(lead)
        sla_horas = SLA_HORAS_POR_ETAPA.get(etapa)
        entrou_etapa_em = lead.atualizado_em
        atrasado = sla_horas is not None and agora - entrou_etapa_em > timedelta(hours=sla_horas)
        cards.append(
            {
                "id": lead.id,
                "nome": lead.nome,
                "empresa": lead.empresa,
                "marca": lead.marca,
                "etapa": etapa,
                "responsavel": getattr(lead.responsavel, "nome", None),
                "proxima_acao_em": lead.proxima_acao_em,
                "status": lead.status.value,
                "entrou_etapa_em": entrou_etapa_em,
                "sla_horas": sla_horas,
                "atrasado": atrasado,
            }
        )
    return {
        "etapas": [{"id": etapa, **dados} for etapa, dados in KANBAN_ETAPAS.items()],
        "cards": cards,
        "acoes": {"gerenciar": usuario.pode("leads.manage")},
    }


async def _atendentes_elegiveis(session: AsyncSession, organizacao_id: int) -> list[UsuarioOperacoes]:
    """Usuários ativos com perfil "comercial" (atendimento/vendas) -- o único perfil
    que corresponde de fato a quem atende lead no dia a dia.

    Achado (10/09/2026): a primeira versão usava "superadmin ou administrador ou
    permissão leads.manage", que na prática incluiu Tech (permissões amplas de
    acesso não implicam papel de atendimento) e distribuiu leads reais para quem
    não deveria recebê-los. Corrigido para usar o campo que reflete o papel de
    negócio, não o nível de acesso ao sistema.
    """
    usuarios = (
        (
            await session.execute(
                select(UsuarioOperacoes)
                .where(
                    UsuarioOperacoes.organizacao_id == organizacao_id,
                    UsuarioOperacoes.ativo.is_(True),
                    UsuarioOperacoes.perfil == "comercial",
                )
                .order_by(UsuarioOperacoes.nome)
            )
        )
        .scalars()
        .all()
    )
    return list(usuarios)


@router.post("/v1/admin/leads/distribuir")
async def distribuir_leads(session: SessionDep, request: Request, usuario: LeadsManageDep) -> dict:
    """Distribui em rodízio os leads abertos sem responsável entre os atendentes
    elegíveis, do mais antigo sem contato para o mais recente -- mesmo critério de
    urgência já usado na ordenação do kanban.

    Reaproveita distribuir_lead_automaticamente/obter_politica_crm (app/crm.py):
    mesmo motor e mesmo cursor (PoliticaCRM.ultimo_responsavel_distribuido_id) do
    round-robin automático por lead (achado item 12 da auditoria de CRM,
    06/09/2026) -- este endpoint só aplica o mesmo mecanismo em lote, ao backlog
    de leads que ficou sem responsável antes daquele mecanismo existir ou estar
    ativo, em vez de manter um segundo round-robin com estado próprio.
    """
    atendentes = await _atendentes_elegiveis(session, usuario.organizacao_id)
    if not atendentes:
        raise HTTPException(status_code=422, detail="Nenhum atendente elegível (usuário ativo com perfil comercial) encontrado")
    politica = await obter_politica_crm(session, usuario.organizacao_id)
    leads_sem_responsavel = (
        (
            await session.execute(
                select(Lead)
                .where(
                    Lead.organizacao_id == usuario.organizacao_id,
                    Lead.arquivado_em.is_(None),
                    Lead.responsavel_id.is_(None),
                    Lead.status.not_in((StatusLead.CONVERTIDO, StatusLead.DESCARTADO)),
                )
                .order_by(Lead.criado_em.asc(), Lead.id.asc())
            )
        )
        .scalars()
        .all()
    )
    nomes_por_id = {item.id: item.nome for item in atendentes}
    por_responsavel: dict[str, int] = {}
    for lead in leads_sem_responsavel:
        await distribuir_lead_automaticamente(session, lead, politica)
        if lead.responsavel_id is not None:
            nome = nomes_por_id.get(lead.responsavel_id, f"usuário {lead.responsavel_id}")
            por_responsavel[nome] = por_responsavel.get(nome, 0) + 1
    distribuidos = sum(por_responsavel.values())
    _auditar(
        session,
        usuario,
        request,
        "distribuir_leads",
        "leads:rodizio",
        {"distribuidos": distribuidos, "por_responsavel": por_responsavel},
    )
    await session.commit()
    return {
        "distribuidos": distribuidos,
        "por_responsavel": por_responsavel,
        "atendentes": [{"id": item.id, "nome": item.nome} for item in atendentes],
    }


class NovoClienteMoverInput(BaseModel):
    nome: str = Field(min_length=2, max_length=150)
    email: str | None = Field(default=None, max_length=254)
    telefone: str = Field(default="", max_length=30)
    empresa: str | None = Field(default=None, max_length=200)

    @field_validator("nome")
    @classmethod
    def limpar_nome(cls, valor: str) -> str:
        return valor.strip()


class MoverPesquisaInput(BaseModel):
    """Corrige o vínculo de uma pesquisa que caiu no lead errado -- achado real
    (10/09/2026): e-mail genérico reaproveitado para clientes particulares
    diferentes misturou pesquisas de empresas distintas no mesmo lead."""

    lead_id_destino: int | None = Field(default=None, ge=1)
    novo_cliente: NovoClienteMoverInput | None = None

    @model_validator(mode="after")
    def exigir_um_destino(self) -> "MoverPesquisaInput":
        if bool(self.lead_id_destino) == bool(self.novo_cliente):
            raise ValueError("Informe o lead de destino ou os dados de um novo cliente, não os dois")
        return self


@router.post("/v1/admin/pesquisas/{pesquisa_id}/mover-lead")
async def mover_pesquisa_para_outro_lead(
    pesquisa_id: str,
    dados: MoverPesquisaInput,
    session: SessionDep,
    usuario: LeadsManageDep,
    request: Request,
) -> dict:
    """Corrige uma pesquisa vinculada ao cliente errado sem apagar nenhum dado:
    a pesquisa muda de lead, o lead de origem e o de destino continuam intactos
    (o de origem só perde essa pesquisa da lista)."""
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
    lead_origem_id = pesquisa.lead_id
    if dados.lead_id_destino is not None:
        if dados.lead_id_destino == lead_origem_id:
            raise HTTPException(status_code=422, detail="A pesquisa já está vinculada a esse cliente")
        lead_destino = (
            await session.execute(
                select(Lead).where(
                    Lead.id == dados.lead_id_destino,
                    Lead.organizacao_id == usuario.organizacao_id,
                    Lead.arquivado_em.is_(None),
                )
            )
        ).scalar_one_or_none()
        if lead_destino is None:
            raise HTTPException(status_code=422, detail="Lead de destino inválido")
    else:
        novo = dados.novo_cliente
        assert novo is not None  # garantido por exigir_um_destino
        empresa = await obter_ou_criar_empresa(session, usuario.organizacao_id, novo.empresa)
        if novo.email:
            email = novo.email.strip().lower()
        elif novo.telefone.strip():
            email = email_sintetico_por_telefone(novo.telefone)
        else:
            # Sem e-mail nem telefone: usa o id da própria pesquisa (sempre
            # único, é um uuid4) direto no e-mail sintético -- passar por
            # email_sintetico_por_telefone (extrai só dígitos) derrubaria a
            # unicidade sempre que o id sorteado tivesse poucos ou nenhum
            # dígito, reproduzindo o próprio bug que este endpoint existe
            # para corrigir.
            email = f"pesquisa-{pesquisa.id}@{DOMINIO_CLIENTE_SEM_EMAIL}"
        # uq_leads_org_email_ativos (migração h58f0d4c9e31) proíbe dois leads
        # ativos com o mesmo e-mail na mesma organização -- se esse e-mail já
        # é de outro cliente ativo, reaproveita o lead existente em vez de
        # tentar inserir um novo e derrubar com 500.
        lead_destino = await buscar_lead_ativo_por_email(session, usuario.organizacao_id, email)
        if lead_destino is not None:
            if lead_destino.id == lead_origem_id:
                raise HTTPException(
                    status_code=422,
                    detail="Já existe um cliente ativo com esse e-mail/telefone — é o mesmo desta pesquisa",
                )
            pesquisa.lead_id = lead_destino.id
            pesquisa.empresa_id = lead_destino.empresa_id
            _auditar(
                session,
                usuario,
                request,
                "mover_pesquisa",
                f"pesquisa:{pesquisa.id}",
                {"lead_origem_id": lead_origem_id, "lead_destino_id": lead_destino.id, "cliente_ja_existia": True},
            )
            await session.commit()
            return {
                "pesquisa_id": pesquisa.id,
                "lead_origem_id": lead_origem_id,
                "lead_destino_id": lead_destino.id,
                "lead_destino_nome": lead_destino.nome,
                "cliente_ja_existia": True,
            }
        lead_destino = Lead(
            organizacao_id=usuario.organizacao_id,
            empresa_id=empresa.id if empresa else None,
            nome=novo.nome,
            empresa=empresa.nome if empresa else None,
            email=email,
            telefone=novo.telefone.strip(),
            marca=pesquisa.marca,
            atividade=pesquisa.atividade,
            origem="operador",
            tipo_interesse=TipoProcesso.MARCA,
            aceite_privacidade=True,
            aceite_marketing=False,
            responsavel_id=usuario.id,
            status=StatusLead.NOVO,
        )
        registrar_consentimento_operador(lead_destino, usuario.id)
        session.add(lead_destino)
        await session.flush()
    pesquisa.lead_id = lead_destino.id
    pesquisa.empresa_id = lead_destino.empresa_id
    _auditar(
        session,
        usuario,
        request,
        "mover_pesquisa",
        f"pesquisa:{pesquisa.id}",
        {"lead_origem_id": lead_origem_id, "lead_destino_id": lead_destino.id},
    )
    await session.commit()
    return {
        "pesquisa_id": pesquisa.id,
        "lead_origem_id": lead_origem_id,
        "lead_destino_id": lead_destino.id,
        "lead_destino_nome": lead_destino.nome,
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
    if dados.etapa == "perdidos":
        raise HTTPException(
            status_code=422,
            detail="Para descartar a oportunidade, informe o motivo da perda (PATCH de status), não arraste o card",
        )
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


# Fases do funil em que uma proposta já foi enviada (usadas em
# _tempo_medio_ate_proposta_dias) — achado L10 do plano Leads/CRM (Fase 2,
# 03/09/2026).
FASES_POS_PROPOSTA: frozenset[str] = frozenset(
    {
        "proposta_enviada",
        "proposta_aceita",
        "aguardando_pagamento",
        "pagamento_confirmado",
        "ganho",
        "protocolo_inpi",
        "processo_inpi",
    }
)


def _tempo_medio_ate_proposta_dias(leads: list[Lead], entradas_proposta: dict[int, datetime]) -> list[float]:
    """Dias de ``Lead.criado_em`` até o envio da proposta, por lead.

    Achado L10 do plano Leads/CRM (Fase 2, 03/09/2026): antes usava
    ``Lead.atualizado_em`` como proxy — um campo tocado por qualquer edição do
    registro (mudar responsável, tag, nota), não só pelo envio da proposta.
    ``entradas_proposta`` vem de ``HistoricoFaseLead`` (evento real da
    transição para a fase "proposta_enviada"); quando não há esse histórico
    (ex.: proposta enviada por um fluxo que ainda não avança a fase do lead),
    cai para ``atualizado_em`` em vez de descartar o lead da métrica.
    """
    valores: list[float] = []
    for item in leads:
        if item.fase not in FASES_POS_PROPOSTA or not item.criado_em:
            continue
        entrada = entradas_proposta.get(item.id) or item.atualizado_em
        if not entrada:
            continue
        valores.append((entrada - item.criado_em).total_seconds() / 86400)
    return valores


def _tempo_medio_primeiro_atendimento_horas(leads: list[Lead], primeiro_contato: dict[int, datetime]) -> list[float]:
    """Horas de ``Lead.criado_em`` até o primeiro ``ContatoLead`` registrado.

    Achado P1 da auditoria de Leads (03/09/2026): não existia nenhuma medida de
    SLA de primeiro atendimento -- o dashboard só media tempo até a proposta
    (uma etapa bem mais adiante no funil). Leads sem nenhum contato registrado
    ainda não entram nessa média (contam à parte, ver ``leads_sem_atendimento``).
    """
    valores: list[float] = []
    for item in leads:
        primeiro = primeiro_contato.get(item.id)
        if primeiro is None or not item.criado_em:
            continue
        valores.append((primeiro - item.criado_em).total_seconds() / 3600)
    return valores


def _consulta_propostas_dashboard(organizacao_id: int):
    """Propostas elegíveis para taxa_pagamento/taxa_protocolo no dashboard.

    Achado L11 do plano Leads/CRM (Fase 2, 03/09/2026): antes o universo era
    TODAS as propostas da organização, inclusive rascunhos nunca enviados e
    propostas de leads já arquivados — denominador inconsistente com as
    demais métricas do mesmo endpoint (que filtram por lead não arquivado).
    """
    return (
        select(PropostaComercial)
        .join(Lead, Lead.id == PropostaComercial.lead_id)
        .where(
            PropostaComercial.organizacao_id == organizacao_id,
            PropostaComercial.status != "rascunho",
            Lead.arquivado_em.is_(None),
        )
    )


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
                # Achado item 42 da auditoria completa do CRM (06/09/2026):
                # so existiam contagens absolutas por operador -- sem taxa,
                # 10 ganhos podem ser ótimo ou péssimo dependendo de quantos
                # fecharam no total.
                "taxa_conversao": round(int(gan) / (int(gan) + int(per)), 4) if (gan or per) else 0,
            }
            for rid, ab, atr, gan, per in prod
        ),
        key=lambda x: (-x["abertas"], -x["ganhos"]),
    )

    por_origem = dict(
        (await session.execute(select(Lead.origem, func.count()).where(*base).group_by(Lead.origem))).all()
    )
    # Achado item 41 da auditoria completa do CRM (06/09/2026):
    # leads_por_origem já existia mas era só volume (todos os leads,
    # inclusive abertos) -- nunca dava pra saber qual origem realmente
    # converte mais, só qual gera mais volume.
    por_origem_resultado = (
        await session.execute(
            select(Lead.origem, Lead.resultado, func.count()).where(*base).group_by(Lead.origem, Lead.resultado)
        )
    ).all()
    resumo_por_origem: dict[str, dict[str, int]] = {}
    for origem_valor, resultado_valor, total_valor in por_origem_resultado:
        chave = origem_valor or "nao_informado"
        bucket = resumo_por_origem.setdefault(chave, {"total": 0, "ganho": 0, "perdido": 0})
        bucket["total"] += int(total_valor)
        if resultado_valor == "ganho":
            bucket["ganho"] += int(total_valor)
        elif resultado_valor == "perdido":
            bucket["perdido"] += int(total_valor)
    conversao_por_origem = [
        {
            "origem": origem_valor,
            "total": bucket["total"],
            "ganho": bucket["ganho"],
            "perdido": bucket["perdido"],
            "taxa_conversao": round(bucket["ganho"] / (bucket["ganho"] + bucket["perdido"]), 4)
            if (bucket["ganho"] or bucket["perdido"])
            else 0,
        }
        for origem_valor, bucket in sorted(resumo_por_origem.items())
    ]
    leads = list((await session.execute(select(Lead).where(*base))).scalars())
    atrasados = sum(
        1
        for item in leads
        if item.status not in (StatusLead.CONVERTIDO, StatusLead.DESCARTADO)
        and item.proxima_acao_em is not None
        and item.proxima_acao_em < agora
    )
    entradas_proposta = dict(
        (
            await session.execute(
                select(HistoricoFaseLead.lead_id, func.min(HistoricoFaseLead.entrou_em))
                .where(
                    HistoricoFaseLead.organizacao_id == org,
                    HistoricoFaseLead.fase == "proposta_enviada",
                )
                .group_by(HistoricoFaseLead.lead_id)
            )
        ).all()
    )
    tempo_ate_proposta = _tempo_medio_ate_proposta_dias(leads, entradas_proposta)
    primeiro_contato = dict(
        (
            await session.execute(
                select(ContatoLead.lead_id, func.min(ContatoLead.criado_em))
                .where(ContatoLead.organizacao_id == org)
                .group_by(ContatoLead.lead_id)
            )
        ).all()
    )
    tempo_primeiro_atendimento = _tempo_medio_primeiro_atendimento_horas(leads, primeiro_contato)
    sem_atendimento = sum(
        1
        for item in leads
        if item.id not in primeiro_contato and item.status not in (StatusLead.CONVERTIDO, StatusLead.DESCARTADO)
    )
    propostas = list((await session.execute(_consulta_propostas_dashboard(org))).scalars())
    aceites = [
        (item.aceito_em - item.enviado_em).total_seconds() / 86400
        for item in propostas
        if item.aceito_em and item.enviado_em
    ]
    # Achado CRM-2/CRM-3 da auditoria (04/09/2026): taxa_pagamento e
    # taxa_protocolo usavam TODAS as propostas nao-rascunho (inclusive
    # recusadas/expiradas/canceladas) como denominador -- inflava a taxa
    # para baixo, ja que proposta recusada nunca teria pagamento nem
    # protocolo por definicao. O universo elegivel para essas duas etapas
    # e so as propostas ACEITAS. taxa_aceite (nova) usa o universo de
    # propostas enviadas (todas nao-rascunho), que e a base correta para
    # medir taxa de aceite.
    #
    # "taxa_contrato" (pedida na auditoria) nao foi adicionada como metrica
    # separada: neste produto, assinar_proposta_portal (app/api/
    # portal_cliente.py) registra a assinatura eletronica (Assinatura
    # PropostaComercial) NO MESMO ATO em que a proposta e aceita -- nao ha
    # uma etapa de "assinar o contrato" distinta e posterior ao aceite hoje.
    # Uma metrica de "taxa_contrato" separada da taxa_aceite seria
    # redundante (sempre igual) e enganosa.
    propostas_aceitas = [item for item in propostas if item.status == "aceita"]
    pagas = sum(1 for item in propostas_aceitas if item.pagamento_status == "confirmado")
    protocoladas = sum(1 for item in propostas_aceitas if item.protocolo_em)

    # Achado item 43 da auditoria completa do CRM (06/09/2026): o funil só
    # mostrava a foto atual (quantos leads estão em cada fase agora), não a
    # taxa de passagem etapa-a-etapa -- 2 leads parados em "novo" pode ser
    # gargalo ou pode ser normal, dependendo de quantos jamais chegaram lá.
    # HistoricoFaseLead não registra a entrada inicial em "novo" (achado
    # item 9, ainda não corrigido) -- por isso a primeira fase usa o total
    # de leads da organização como base, e as demais usam a contagem real
    # de entradas (distinct lead_id, já que HistoricoFaseLead pode ter mais
    # de uma linha por lead/fase em caso de retrocesso e reavanço).
    entradas_por_fase = dict(
        (
            await session.execute(
                select(HistoricoFaseLead.fase, func.count(func.distinct(HistoricoFaseLead.lead_id)))
                .where(HistoricoFaseLead.organizacao_id == org)
                .group_by(HistoricoFaseLead.fase)
            )
        ).all()
    )
    total_leads_org = len(leads)
    conversao_por_etapa = []
    entrada_anterior: int | None = None
    for indice, fase in enumerate(ORDEM_FASE_LEAD):
        entrada_atual = total_leads_org if indice == 0 else int(entradas_por_fase.get(fase, 0))
        conversao_por_etapa.append(
            {
                "fase": fase,
                "label": FASE_LABELS.get(fase, fase),
                "entradas": entrada_atual,
                "taxa_da_etapa_anterior": round(entrada_atual / entrada_anterior, 4)
                if entrada_anterior
                else (1.0 if indice == 0 else 0),
                "taxa_acumulada": round(entrada_atual / total_leads_org, 4) if total_leads_org else 0,
            }
        )
        entrada_anterior = entrada_atual

    # Achado item 48 da auditoria completa do CRM (06/09/2026): não existia
    # nenhuma soma do valor de propostas ainda em aberto -- só receita já
    # faturada (financeiro.py), nunca o que está "na mesa" esperando
    # decisão do cliente. Universo elegível são propostas enviadas/
    # visualizadas (enviadas ao cliente, ainda sem aceite/recusa/expiração/
    # cancelamento) -- mesmo critério de "aberta" usado no resto do
    # dashboard.
    propostas_abertas = [item for item in propostas if item.status in ("enviada", "visualizada")]
    pipeline_previsto = sum((item.honorarios or 0) + (item.taxa_gru or 0) for item in propostas_abertas)

    # Achado item 49 (só depois do 48, pré-requisito do roteiro): pondera
    # cada proposta aberta pela chance histórica de um lead na mesma fase
    # atual chegar a "ganho" -- reaproveita taxa_acumulada já calculada
    # acima (item 43) em vez de inventar um segundo modelo de probabilidade.
    # Sem histórico suficiente (taxa_acumulada de "ganho" ainda zerada),
    # o forecast cai para 0 em vez de uma probabilidade inventada.
    acumulada_por_fase = {item["fase"]: item["taxa_acumulada"] for item in conversao_por_etapa}
    taxa_final_historica = acumulada_por_fase.get(FaseLead.GANHO.value, 0)
    leads_por_id = {item.id: item for item in leads}
    forecast_ponderado = Decimal("0")
    for item in propostas_abertas:
        lead_da_proposta = leads_por_id.get(item.lead_id)
        taxa_da_fase_atual = acumulada_por_fase.get(lead_da_proposta.fase, 0) if lead_da_proposta else 0
        probabilidade = min(1.0, taxa_final_historica / taxa_da_fase_atual) if taxa_da_fase_atual else 0
        forecast_ponderado += ((item.honorarios or 0) + (item.taxa_gru or 0)) * Decimal(str(probabilidade))

    return {
        "funil": funil,
        "resultado": {"aberto": aberto, "ganho": ganho, "perdido": perdido},
        "taxa_conversao": round(ganho / fechados, 4) if fechados else 0,
        "perdas_por_motivo": perdas_por_motivo,
        "produtividade": produtividade,
        "leads_por_origem": {origem or "nao_informado": int(total) for origem, total in por_origem.items()},
        "conversao_por_origem": conversao_por_origem,
        "conversao_por_etapa": conversao_por_etapa,
        "pipeline_previsto": pipeline_previsto,
        "forecast_ponderado": round(forecast_ponderado, 2),
        "atrasos": atrasados,
        "tempo_medio_ate_proposta_dias": round(sum(tempo_ate_proposta) / len(tempo_ate_proposta), 2)
        if tempo_ate_proposta
        else 0,
        "tempo_medio_ate_aceite_dias": round(sum(aceites) / len(aceites), 2) if aceites else 0,
        "tempo_medio_primeiro_atendimento_horas": round(
            sum(tempo_primeiro_atendimento) / len(tempo_primeiro_atendimento), 2
        )
        if tempo_primeiro_atendimento
        else 0,
        "leads_sem_atendimento": sem_atendimento,
        "taxa_aceite": round(len(propostas_aceitas) / len(propostas), 4) if propostas else 0,
        "taxa_pagamento": round(pagas / len(propostas_aceitas), 4) if propostas_aceitas else 0,
        "taxa_protocolo": round(protocoladas / len(propostas_aceitas), 4) if propostas_aceitas else 0,
        "atualizado_em": agora,
    }


@router.get("/v1/admin/leads-dashboard/serie-temporal")
async def serie_temporal_leads(
    session: SessionDep,
    usuario: LeadsViewDep,
    dias: Annotated[int, Query(ge=7, le=180)] = 30,
) -> dict:
    """Leads criados por dia e evolução do funil (achado Fase 4 do roadmap
    pós-auditoria de Leads, 03/09/2026): antes só existiam contagens
    acumuladas (dashboard), sem visão de tendência ao longo do tempo."""
    org = usuario.organizacao_id
    desde = datetime.now(UTC) - timedelta(days=dias)
    # Achado D1 da auditoria de 06/09/2026: func.date(coluna) trunca o
    # timestamptz no timezone da sessao Postgres (UTC), nao no calendario
    # civil de Brasilia -- um lead/transicao perto da virada do dia (ex.:
    # 22h em Brasilia = 01h UTC do dia seguinte) caia no dia errado do
    # agrupamento. func.timezone() converte para o fuso de negocio antes do
    # truncamento (mesmo padrao ja usado em app.api.juridico, achado JUR-1).
    data_lead_brasil = func.date(func.timezone("America/Sao_Paulo", Lead.criado_em))
    data_historico_brasil = func.date(func.timezone("America/Sao_Paulo", HistoricoFaseLead.entrou_em))

    criados_por_dia = dict(
        (
            await session.execute(
                select(data_lead_brasil, func.count())
                .where(Lead.organizacao_id == org, Lead.criado_em >= desde)
                .group_by(data_lead_brasil)
            )
        ).all()
    )
    entradas_por_dia_e_fase = (
        await session.execute(
            select(data_historico_brasil, HistoricoFaseLead.fase, func.count())
            .where(HistoricoFaseLead.organizacao_id == org, HistoricoFaseLead.entrou_em >= desde)
            .group_by(data_historico_brasil, HistoricoFaseLead.fase)
        )
    ).all()
    funil_por_dia: dict[str, dict[str, int]] = {}
    for data_evento, fase, total in entradas_por_dia_e_fase:
        funil_por_dia.setdefault(data_evento.isoformat(), {})[fase] = int(total)

    hoje = datetime.now(UTC).astimezone(FUSO_BRASIL).date()
    serie = []
    for offset in range(dias, -1, -1):
        dia = hoje - timedelta(days=offset)
        chave = dia.isoformat()
        funil_dia = {fase: 0 for fase in ORDEM_FASE_LEAD}
        funil_dia.update(funil_por_dia.get(chave, {}))
        serie.append(
            {
                "data": chave,
                "leads_criados": int(criados_por_dia.get(dia, 0)),
                "funil": funil_dia,
            }
        )
    return {"dias": dias, "serie": serie}


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
            # Achado H4/P1 da auditoria (10/09/2026): nada exigia motivo_perda
            # ao descartar -- o backend aceitava a oportunidade fechada sem
            # nenhum motivo estruturado, quebrando os relatórios de perda.
            if not dados.motivo_perda:
                raise HTTPException(
                    status_code=422, detail="Motivo de perda é obrigatório ao descartar a oportunidade"
                )
            if dados.motivo_perda not in MOTIVOS_PERDA:
                raise HTTPException(status_code=422, detail="Motivo de perda inválido")
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
    responsavel_notificar: UsuarioOperacoes | None = None
    if "responsavel_id" in dados.model_fields_set:
        responsavel = None
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
        # Achado P2 da auditoria de Leads: nenhuma notificação avisava o novo
        # responsável quando um lead era atribuído a ele.
        if responsavel is not None and dados.responsavel_id != lead.responsavel_id:
            responsavel_notificar = responsavel
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
        await aplicar_cadencias_automaticas(
            session, lead, "status", lead.status.value, por=usuario.nome or "sistema"
        )
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
    # Achado (10/09/2026, distribuicao de leads): session.refresh(lead) sozinho
    # expira o relacionamento "responsavel" sem recarrega-lo -- o acesso
    # sincrono a lead.responsavel.nome em _lead_response tentava um lazy load
    # fora do bridge async/greenlet e derrubava o endpoint com 500
    # (MissingGreenlet), sempre que responsavel_id mudava (inclusive para
    # None). selectinload explicito evita o lazy load.
    lead = (
        await session.execute(select(Lead).options(selectinload(Lead.responsavel)).where(Lead.id == lead.id))
    ).scalar_one()
    if responsavel_notificar is not None:
        await enviar_alerta_lead_atribuido(
            responsavel_notificar.nome, responsavel_notificar.email, lead.nome, lead.marca, lead.id
        )
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


@router.post("/v1/admin/leads/{lead_id}/enviar-email-prospeccao", status_code=status.HTTP_201_CREATED)
async def enviar_email_prospeccao(
    lead_id: int,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
    _limite: AcaoAdminDep,
) -> dict:
    """Envia ao lead o e-mail comercial configurado em Configuração > Modelo de
    e-mail (leads), a partir do botão "Enviar e-mail" no card do lead
    ("Abrir contato"). Registra o envio como um contato (canal e-mail) no
    histórico do lead, igual a qualquer outra interação."""
    lead = await _lead_do_operador(lead_id, session, usuario)
    if not lead.email:
        raise HTTPException(status_code=422, detail="Este lead nao tem e-mail cadastrado")
    org = await session.get(Organizacao, usuario.organizacao_id)
    config = config_email_leads(org)
    assunto = config["assunto"].replace("{{lead.nome}}", lead.nome)
    corpo = config["corpo"].replace("{{lead.nome}}", lead.nome)
    try:
        await enviar_email_prospeccao_lead(lead.email, assunto, corpo, reply_to=config.get("reply_to") or usuario.email)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Nao foi possivel enviar o e-mail agora. Tente novamente.") from exc
    contato = ContatoLead(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead_id,
        empresa_id=lead.empresa_id,
        operador_id=usuario.id,
        operador_nome=usuario.nome,
        canal=CanalContato.EMAIL,
        resultado="E-mail comercial enviado",
        observacao=f"Assunto: {assunto}",
    )
    session.add(contato)
    lead.ultimo_contato_em = datetime.now(UTC)
    registrar_evento_operacional(
        session,
        organizacao_id=usuario.organizacao_id,
        dominio="crm",
        tipo="crm.email_prospeccao_enviado",
        entidade_tipo="lead",
        entidade_id=lead_id,
        ator=usuario.ator,
        ator_id=usuario.id,
        payload={"contato_id": contato.id, "destinatario": lead.email},
    )
    _auditar(session, usuario, request, "enviar_email", f"lead:{lead_id}", {"destinatario": lead.email})
    await session.commit()
    await session.refresh(contato)
    return _contato_response(contato, None, lead.empresa)


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


@router.get("/v1/admin/leads/{lead_id}/score")
async def score_lead(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    """Score simples de fit + engajamento (item 30 da auditoria completa do
    CRM, 06/09/2026). Endpoint dedicado (em vez de campo no LeadResponse
    usado pela lista/kanban) de propósito: calcular exige consultas extras
    por lead (contatos, respostas de e-mail, propostas) -- expor no
    LeadResponse causaria N+1 em toda listagem de leads."""
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    return await calcular_score_lead(session, lead)


def _sugestao_ia_dict(sugestao: SugestaoIALead) -> dict:
    return {
        "id": sugestao.id,
        "lead_id": sugestao.lead_id,
        "gerado_em": sugestao.gerado_em,
        "modelo": sugestao.modelo,
        "resumo": sugestao.resumo,
        "sugestao_proxima_acao": sugestao.sugestao_proxima_acao,
        "status": sugestao.status,
        "revisado_por": sugestao.revisado_por,
        "revisado_em": sugestao.revisado_em,
        "erro": sugestao.erro,
    }


@router.get("/v1/admin/leads/{lead_id}/sugestao-ia")
async def obter_sugestao_ia(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    """Sugestão mais recente da IA em sombra (resumo + próxima ação) para o
    lead -- nunca contém rascunho de mensagem nem foi enviada a ninguém,
    é só apoio de leitura para o operador decidir sozinho o que fazer."""
    sugestao = (
        await session.execute(
            select(SugestaoIALead)
            .where(SugestaoIALead.lead_id == lead_id, SugestaoIALead.organizacao_id == usuario.organizacao_id)
            .order_by(SugestaoIALead.gerado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if sugestao is None:
        raise HTTPException(status_code=404, detail="Nenhuma sugestão de IA gerada para este lead ainda")
    return _sugestao_ia_dict(sugestao)


class RevisaoSugestaoIAInput(BaseModel):
    status: Literal["aprovada", "descartada"]


@router.post("/v1/admin/leads/{lead_id}/sugestao-ia/{sugestao_id}/revisar")
async def revisar_sugestao_ia(
    lead_id: int, sugestao_id: int, dados: RevisaoSugestaoIAInput, session: SessionDep, usuario: LeadsManageDep
) -> dict:
    """Registra que um humano revisou a sugestão -- puramente trilha de
    auditoria, nunca dispara nenhum envio nem ação automática."""
    sugestao = (
        await session.execute(
            select(SugestaoIALead).where(
                SugestaoIALead.id == sugestao_id,
                SugestaoIALead.lead_id == lead_id,
                SugestaoIALead.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if sugestao is None:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada")
    sugestao.status = dados.status
    sugestao.revisado_por = usuario.ator
    sugestao.revisado_em = datetime.now(UTC)
    await session.commit()
    return _sugestao_ia_dict(sugestao)


def _qualificacao_ia_dict(qualificacao: QualificacaoIALead) -> dict:
    return {
        "id": qualificacao.id,
        "lead_id": qualificacao.lead_id,
        "gerado_em": qualificacao.gerado_em,
        "modelo": qualificacao.modelo,
        "prioridade": qualificacao.prioridade,
        "observacao": qualificacao.observacao,
        "status": qualificacao.status,
        "revisado_por": qualificacao.revisado_por,
        "revisado_em": qualificacao.revisado_em,
        "erro": qualificacao.erro,
    }


@router.get("/v1/admin/leads/{lead_id}/qualificacao-ia")
async def obter_qualificacao_ia(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    """Qualificação da IA em sombra gerada uma única vez, no momento em que
    o lead chegou (formulário público ou conversão do Radar de
    Prospecção) -- prioridade sugerida + observação, puramente informativo
    para o comercial priorizar."""
    qualificacao = (
        await session.execute(
            select(QualificacaoIALead).where(
                QualificacaoIALead.lead_id == lead_id, QualificacaoIALead.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if qualificacao is None:
        raise HTTPException(status_code=404, detail="Nenhuma qualificação de IA gerada para este lead ainda")
    return _qualificacao_ia_dict(qualificacao)


class RevisaoQualificacaoIAInput(BaseModel):
    status: Literal["aprovada", "descartada"]


@router.post("/v1/admin/leads/{lead_id}/qualificacao-ia/{qualificacao_id}/revisar")
async def revisar_qualificacao_ia(
    lead_id: int, qualificacao_id: int, dados: RevisaoQualificacaoIAInput, session: SessionDep, usuario: LeadsManageDep
) -> dict:
    qualificacao = (
        await session.execute(
            select(QualificacaoIALead).where(
                QualificacaoIALead.id == qualificacao_id,
                QualificacaoIALead.lead_id == lead_id,
                QualificacaoIALead.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if qualificacao is None:
        raise HTTPException(status_code=404, detail="Qualificação não encontrada")
    qualificacao.status = dados.status
    qualificacao.revisado_por = usuario.ator
    qualificacao.revisado_em = datetime.now(UTC)
    await session.commit()
    return _qualificacao_ia_dict(qualificacao)


@router.get("/v1/admin/leads/{lead_id}/relacionados")
async def leads_relacionados(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    """Outras oportunidades do mesmo contato (mesmo e-mail ou telefone).

    Achado P1 da auditoria de Leads (03/09/2026): como não existe uma entidade
    de contato central, a mesma pessoa interessada em marcas diferentes vira
    leads separados sem nenhum vínculo visível em tela nenhuma. Este endpoint
    não muda o modelo de dados -- só torna visível uma relação que já existe
    implicitamente (mesmo e-mail/telefone), com a mesma normalização de
    telefone usada na deduplicação do formulário público.
    """
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    telefone_digitos = "".join(caractere for caractere in lead.telefone if caractere.isdigit())
    relacionados = (
        (
            await session.execute(
                select(Lead)
                .where(
                    Lead.organizacao_id == usuario.organizacao_id,
                    Lead.id != lead.id,
                    Lead.arquivado_em.is_(None),
                    or_(
                        func.lower(Lead.email) == lead.email.lower(),
                        func.regexp_replace(Lead.telefone, r"\D", "", "g") == telefone_digitos,
                    ),
                )
                .order_by(Lead.criado_em.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total": len(relacionados),
        "itens": [
            {
                "id": item.id,
                "marca": item.marca,
                "status": item.status.value,
                "fase": item.fase,
                "resultado": item.resultado,
                "criado_em": item.criado_em,
            }
            for item in relacionados
        ],
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
    # Achado do usuário (17/09/2026): mover um lead manualmente para
    # "Pagamento confirmado" (ou fases seguintes) não exigia nenhuma
    # contratação financeira vinculada -- um lead podia chegar a "Ganho"
    # sem nunca ter passado por uma proposta aceita, sem gerar nenhum
    # ContratacaoServico/LancamentoFinanceiro, com o financeiro achando que
    # tudo estava certo (o próprio funil marcava a etapa como concluída só
    # pela posição, ver renderFunil em admin-leads.js). Mesmo padrão de
    # trava já usado acima para protocolo_inpi/processo_inpi (documentos).
    if ORDEM_FASE_LEAD.index(dados.fase.value) >= ORDEM_FASE_LEAD.index(FaseLead.PAGAMENTO_CONFIRMADO.value):
        tem_contratacao = (
            await session.execute(
                select(ContratacaoServico.id)
                .where(ContratacaoServico.organizacao_id == usuario.organizacao_id, ContratacaoServico.lead_id == lead.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if tem_contratacao is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Etapa bloqueada. Este lead não tem nenhuma contratação financeira vinculada -- "
                    "gere uma proposta aceita ou registre a contratação em Financeiro antes de avançar "
                    "para esta fase."
                ),
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


def _prazo_sla_24h(inicio: datetime) -> datetime:
    """Prazo operacional explícito de 24 horas corridas após liberar o
    protocolo -- usado tanto por rotas de documentos (aqui) quanto pelas de
    propostas (app.api.leads_propostas)."""
    return inicio + timedelta(hours=24)


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
                "tem_arquivo": bool(por_tipo[t].caminho) if t in por_tipo else False,
                "tamanho": por_tipo[t].tamanho if t in por_tipo else None,
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


@router.post("/v1/admin/leads/{lead_id}/documentos/{tipo}/arquivo", status_code=status.HTTP_201_CREATED)
async def enviar_arquivo_documento_lead(
    lead_id: int,
    tipo: str,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
    arquivo: Annotated[UploadFile, File()],
) -> dict:
    """Achado do usuário (21/09/2026): "Etapa bloqueada. Documentos
    obrigatórios pendentes: procuração", sem nenhum lugar pra anexar o
    arquivo -- DocumentoLead sempre foi só metadado. Mesmo padrão de
    validação/armazenamento de app.api.portal_cliente.enviar_material_marca_admin.
    """
    if tipo not in TIPOS_DOCUMENTO_LEAD:
        raise HTTPException(status_code=422, detail="Tipo de documento inválido")
    await _lead_da_org(session, lead_id, usuario.organizacao_id)
    if arquivo.size and arquivo.size > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo maior que 15 MB")
    conteudo = await arquivo.read()
    if len(conteudo) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo maior que 15 MB")
    await escanear_upload_ou_rejeitar(conteudo)
    nome = f"{secrets.token_hex(12)}-{Path(arquivo.filename or 'arquivo').name}"
    try:
        caminho = save_bytes(f"documentos-lead/{usuario.organizacao_id}/{lead_id}/{tipo}/{nome}", conteudo)
    except StorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    novo_hash = hashlib.sha256(conteudo).hexdigest()
    documento = (
        await session.execute(
            select(DocumentoLead).where(
                DocumentoLead.lead_id == lead_id,
                DocumentoLead.organizacao_id == usuario.organizacao_id,
                DocumentoLead.tipo == tipo,
            )
        )
    ).scalar_one_or_none()
    if documento is None:
        documento = DocumentoLead(organizacao_id=usuario.organizacao_id, lead_id=lead_id, tipo=tipo)
        session.add(documento)
    else:
        # Achado do Codex review (PR #90): trocar o arquivo de um documento
        # que já existia precisa do mesmo tratamento de "mudou_conteudo" de
        # salvar_documentos_lead -- versiona o estado anterior e invalida
        # uma assinatura clickwrap existente (assinar_documento_portal só
        # hasheia metadado, não o arquivo; sem isso o portal continuaria
        # mostrando "assinado" com o conteúdo trocado por baixo).
        session.add(
            VersaoDocumentoLead(
                organizacao_id=documento.organizacao_id,
                documento_id=documento.id,
                versao=documento.versao,
                hash_documento=documento.hash_documento
                or hashlib.sha256(
                    f"{documento.tipo}|{documento.numero or ''}|{documento.data or ''}|{documento.status}|"
                    f"{documento.observacoes or ''}|{documento.validade_em or ''}".encode()
                ).hexdigest(),
                conteudo={
                    "tipo": documento.tipo,
                    "numero": documento.numero,
                    "data": documento.data.isoformat() if documento.data else None,
                    "status": documento.status,
                    "observacoes": documento.observacoes,
                    "obrigatorio": documento.obrigatorio,
                    "validade_em": documento.validade_em.isoformat() if documento.validade_em else None,
                    "arquivo_hash": documento.arquivo_hash,
                },
            )
        )
        documento.versao += 1
        documento.hash_documento = None
        documento.assinado_em = None
        documento.assinado_ip_hash = None
        documento.assinado_por_cliente_id = None
    documento.caminho = caminho
    documento.content_type = arquivo.content_type
    documento.tamanho = len(conteudo)
    documento.arquivo_hash = novo_hash
    # O upload por si só já satisfaz o gate de avanço de fase
    # (DOCUMENTOS_VALIDOS) -- sem isso o operador precisaria também lembrar
    # de trocar o status manualmente. Nunca rebaixa um status já válido
    # (ex.: "aprovado" por revisão jurídica); qualquer outro valor, incluindo
    # os legados que o dropdown antigo oferecia (em_andamento/concluido/
    # nao_aplicavel -- achado do Codex review, PR #90), nunca satisfazia o
    # gate mesmo assim, então também vira "recebido".
    if documento.status not in DOCUMENTOS_VALIDOS:
        documento.status = "recebido"
    # Mesma reconciliação de SLA que salvar_documentos_lead já faz: o
    # upload pode ser justamente o último documento pendente que libera o
    # protocolo (achado do Codex review, PR #90) -- sem isso sla_inicio_em/
    # sla_prazo_em ficavam nulos e o prazo de 24h nunca começava a contar.
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
    _auditar(session, usuario, request, "arquivo_documento", f"lead:{lead_id}:{tipo}", {})
    await session.commit()
    return {"tipo": tipo, "status": documento.status, "tamanho": documento.tamanho}


@router.get("/v1/admin/leads/{lead_id}/documentos/{tipo}/arquivo")
async def baixar_arquivo_documento_lead(
    lead_id: int, tipo: str, request: Request, session: SessionDep, usuario: LeadsViewDep
) -> StreamingResponse:
    if tipo not in TIPOS_DOCUMENTO_LEAD:
        raise HTTPException(status_code=422, detail="Tipo de documento inválido")
    await _lead_da_org(session, lead_id, usuario.organizacao_id)
    documento = (
        await session.execute(
            select(DocumentoLead).where(
                DocumentoLead.lead_id == lead_id,
                DocumentoLead.organizacao_id == usuario.organizacao_id,
                DocumentoLead.tipo == tipo,
            )
        )
    ).scalar_one_or_none()
    if documento is None or not documento.caminho:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    if documento.caminho.startswith("s3://"):
        try:
            conteudo = read_bytes(documento.caminho)
        except (StorageError, OSError) as exc:
            raise HTTPException(status_code=404, detail="Documento não encontrado") from exc
    else:
        caminho = Path(documento.caminho).resolve()
        base = (local_root() / "documentos-lead" / str(usuario.organizacao_id) / str(lead_id) / tipo).resolve()
        if not caminho.is_file() or base not in caminho.parents:
            raise HTTPException(status_code=404, detail="Documento não encontrado")
        conteudo = caminho.read_bytes()
    _auditar(session, usuario, request, "baixar_documento", f"lead:{lead_id}:{tipo}", {})
    await session.commit()
    return StreamingResponse(
        iter([conteudo]),
        media_type=documento.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{tipo}-{lead_id}"'},
    )


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
    "qualificado": ("Confirmar interesse real", "Validar viabilidade preliminar"),
    "relatorio_enviado": ("Gerar relatório de viabilidade", "Enviar relatório ao cliente"),
    "proposta_enviada": ("Elaborar proposta comercial", "Enviar proposta ao cliente"),
    "proposta_aceita": ("Confirmar aceite da proposta", "Coletar dados para a procuração"),
    "aguardando_pagamento": ("Emitir cobrança",),
    "pagamento_confirmado": ("Confirmar recebimento do pagamento",),
    "ganho": ("Registrar oportunidade como ganha",),
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


# Guias do INPI (GRU) por lead -- movido para app/api/leads_guias.py (Fase
# 4 da missão de maturidade técnica, 14/09/2026). _lead_da_org acima
# continua aqui por ser usado por outras seções deste arquivo.


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
    if not cadencia.ativo:
        raise HTTPException(status_code=422, detail="Cadência inativa")
    total_passos = len(cadencia.passos)
    # Loop e idempotência (LembreteCRM + EnvioCadenciaEmail para passos de
    # e-mail, ver Fase 9 do plano Leads/CRM) ficam em app.crm::aplicar_cadencia_a_lead
    # -- compartilhado com o gatilho automático (achado P2 da auditoria de Leads).
    criados = await aplicar_cadencia_a_lead(session, lead, cadencia, usuario.nome or "sistema", ator_id=usuario.id)
    await session.commit()
    return {"criados": criados, "ignorados_idempotentes": total_passos - criados}


@router.get("/v1/admin/leads/{lead_id}/envios-cadencia")
async def listar_envios_cadencia_lead(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    # Achado do usuário (08/09/2026): o motor de cadência já grava status,
    # abertura e resposta de cada e-mail (EnvioCadenciaEmail), mas nenhuma
    # tela expunha isso pro operador -- os dados existiam, só ficavam
    # invisíveis. Mostra o histórico de envios do lead, mais recente primeiro.
    lead = (
        await session.execute(select(Lead.id).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Oportunidade não encontrada")
    linhas = (
        await session.execute(
            select(EnvioCadenciaEmail, Cadencia.nome, CadenciaPasso.titulo, CadenciaPasso.dia)
            .join(Cadencia, Cadencia.id == EnvioCadenciaEmail.cadencia_id)
            .join(CadenciaPasso, CadenciaPasso.id == EnvioCadenciaEmail.passo_id)
            .where(EnvioCadenciaEmail.lead_id == lead_id, EnvioCadenciaEmail.organizacao_id == usuario.organizacao_id)
            .order_by(EnvioCadenciaEmail.agendado_para.desc())
        )
    ).all()
    return {
        "itens": [
            {
                "id": envio.id,
                "cadencia_nome": cadencia_nome,
                "passo_titulo": passo_titulo,
                "passo_dia": passo_dia,
                "status": envio.status,
                "agendado_para": envio.agendado_para,
                "enviado_em": envio.enviado_em,
                "aberto_em": envio.aberto_em,
                "respondido_em": envio.respondido_em,
                "pausado_em": envio.pausado_em,
                "tentativas": envio.tentativas,
                "ultimo_erro": envio.ultimo_erro,
            }
            for envio, cadencia_nome, passo_titulo, passo_dia in linhas
        ]
    }


@router.get("/v1/cadencias/rastreio/{token}.gif", include_in_schema=False)
async def rastreio_abertura_cadencia(token: str, session: SessionDep) -> Response:
    # Pixel 1x1 transparente. Best-effort: bloqueadores de imagem e proxies de
    # e-mail (ex.: Gmail) podem distorcer esse sinal -- limitação conhecida da
    # indústria toda, não corrigível do nosso lado.
    PIXEL_GIF = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00"
        b"\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    )
    try:
        await registrar_abertura(session, token)
        await session.commit()
    except Exception:
        await session.rollback()
    return Response(content=PIXEL_GIF, media_type="image/gif")


@router.get("/v1/cadencias/descadastrar/{token}", response_class=HTMLResponse, include_in_schema=False)
async def descadastrar_cadencia(token: str, session: SessionDep) -> HTMLResponse:
    # Achado da auditoria completa do CRM (06/09/2026): link de descadastro
    # no rodapé do e-mail de cadência. Página simples, sem exigir login --
    # é assim que qualquer opt-out de e-mail no mercado funciona.
    try:
        await processar_descadastro_cadencia(session, token)
        await session.commit()
    except Exception:
        await session.rollback()
    return HTMLResponse(
        "<!doctype html><html lang='pt-BR'><meta charset='utf-8'>"
        "<body style='font-family:Arial,sans-serif;max-width:520px;margin:80px auto;"
        "color:#10251d;text-align:center;'>"
        "<p style='color:#08704d;font-weight:700;letter-spacing:.08em;'>ZÉ REGISTRA®</p>"
        "<h1 style='font-size:1.2rem;'>Descadastro concluído</h1>"
        "<p>Você não receberá mais e-mails desta sequência de atendimento.</p>"
        "</body></html>"
    )


FASE_LABELS: dict[str, str] = {
    "contato_inicial": "Contato inicial",
    "qualificado": "Qualificado",
    "relatorio_enviado": "Relatório enviado",
    "proposta_enviada": "Proposta enviada",
    "proposta_aceita": "Proposta aceita",
    "aguardando_pagamento": "Aguardando pagamento",
    "pagamento_confirmado": "Pagamento confirmado",
    "ganho": "Ganho",
    "protocolo_inpi": "Protocolo INPI",
    "processo_inpi": "Processo no INPI",
}


def _para_dt(valor) -> datetime:
    """Normaliza date/datetime para datetime aware (UTC) para ordenação."""
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    return datetime.combine(valor, datetime.min.time(), tzinfo=UTC)


def _resumir_alteracoes(detalhes: dict) -> str | None:
    """Renderiza o dict de auditoria (de/para por campo, ou marcadores simples)
    de forma legível para a timeline. Achado P2 da auditoria de Leads."""
    if not detalhes:
        return None
    partes: list[str] = []
    for campo, valor in detalhes.items():
        if isinstance(valor, dict) and "de" in valor and "para" in valor:
            partes.append(f"{campo}: {valor['de']} → {valor['para']}")
        else:
            partes.append(f"{campo}: {valor}")
    return "; ".join(partes) or None


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
    # Achado item 21 da auditoria completa do CRM (06/09/2026): antes só se
    # sabia QUE o lead respondeu um e-mail de cadência (pausa registrada em
    # EnvioCadenciaEmail), nunca O QUE ele escreveu.
    respostas_email = (
        (
            await session.execute(
                select(RespostaEmailLead).where(
                    RespostaEmailLead.lead_id == lead_id, RespostaEmailLead.organizacao_id == org
                )
            )
        )
        .scalars()
        .all()
    )
    for resposta in respostas_email:
        eventos.append(
            {
                "tipo": "resposta_email",
                "data": resposta.recebido_em,
                "titulo": f"Resposta por e-mail · {resposta.assunto}" if resposta.assunto else "Resposta por e-mail",
                "detalhe": resposta.corpo,
                "autor": resposta.remetente,
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
    # Achado P2 da auditoria de Leads (03/09/2026): a timeline já unificava
    # ContatoLead/HistoricoFaseLead/pesquisas/propostas/EventoDominio, mas
    # deixava de fora o log de auditoria genérico (EventoAuditoria) -- é onde
    # ficam registradas trocas de responsável, edições de campo e
    # arquivamento/restauração. "registrar_contato"/"documentos_lead" ficam de
    # fora aqui porque já aparecem via ContatoLead/DocumentoLead acima.
    auditoria = (
        (
            await session.execute(
                select(EventoAuditoria).where(
                    EventoAuditoria.organizacao_id == org,
                    EventoAuditoria.recurso == f"lead:{lead_id}",
                    EventoAuditoria.acao.in_(("alterar", "arquivar", "restaurar")),
                )
            )
        )
        .scalars()
        .all()
    )
    TITULOS_AUDITORIA = {"alterar": "Lead editado", "arquivar": "Lead arquivado", "restaurar": "Lead restaurado"}
    for item in auditoria:
        eventos.append(
            {
                "tipo": f"auditoria_{item.acao}",
                "data": item.criado_em,
                "titulo": TITULOS_AUDITORIA.get(item.acao, item.acao),
                "detalhe": _resumir_alteracoes(item.detalhes),
                "autor": item.ator,
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
    # Mesmo achado de MissingGreenlet do PATCH de lead (10/09/2026): refresh()
    # sozinho expira o relacionamento "responsavel" sem recarregá-lo.
    lead = (
        await session.execute(select(Lead).options(selectinload(Lead.responsavel)).where(Lead.id == lead.id))
    ).scalar_one()
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


LIMITE_COPIA_EMAILS = 500


@router.get("/v1/admin/leads-emails")
async def listar_emails_leads(
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
) -> dict:
    """E-mails dos leads que batem com o filtro atual da tela, para copiar em lote
    (ex.: colar no campo "Para"/"Cco" do cliente de e-mail ao enviar uma proposta em
    massa). Mesma permissao e mesmos filtros do CSV -- so devolve o campo email."""
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
    emails = (
        (
            await session.execute(
                select(Lead.email)
                .distinct()
                .where(*filtros, Lead.email.is_not(None), Lead.email != "")
                .order_by(Lead.email)
                .limit(LIMITE_COPIA_EMAILS)
            )
        )
        .scalars()
        .all()
    )
    _auditar(session, usuario, request, "copiar_email", "leads:emails", {"quantidade": len(emails)})
    await session.commit()
    return {"emails": emails, "total": len(emails), "limite": LIMITE_COPIA_EMAILS}

import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.juridico import TIPOS_PRAZO
from app.api.leads import DOCUMENTOS_VALIDOS
from app.api.leads_propostas import (
    aceite_ja_registrado,
    criar_contratacao_automatica_proposta,
    motivo_bloqueio_aceite,
)
from app.auth import exigir_permissao, hash_ip, hash_senha, hash_token, verificar_senha
from app.clicksign import configuracao as configuracao_clicksign
from app.crm import registrar_evento_operacional
from app.database import get_session, session_factory
from app.emailing import enviar_codigo_confirmacao_portal, enviar_recuperacao_portal
from app.malware_scan import escanear_upload_ou_rejeitar
from app.marca import nome_escritorio_para_email
from app.models import (
    ArquivoClientePortal,
    AssinaturaDocumentoLead,
    AssinaturaPropostaComercial,
    ClientePortal,
    CodigoConfirmacaoPortal,
    DocumentoLead,
    EventoAuditoria,
    FaseLead,
    GuiaInpi,
    HistoricoFaseLead,
    LancamentoFinanceiro,
    Lead,
    MaterialMarcaCliente,
    MensagemClientePortal,
    Organizacao,
    ParcelaFinanceira,
    PrazoJuridico,
    Processo,
    ProcessoMonitorado,
    PropostaComercial,
    RecuperacaoClientePortal,
    SessaoClientePortal,
    VersaoDocumentoLead,
)
from app.proxy import cliente_ip, requisicao_https
from app.ratelimit import RateLimiter
from app.settings import get_settings
from app.storage import StorageError, delete_object, local_root, read_bytes, save_bytes
from app.tenancy import aplicar_contexto_tenant

logger = logging.getLogger("ze_registra.portal_cliente")
router = APIRouter(tags=["portal-cliente"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClientManageDep = Annotated[object, Depends(exigir_permissao("leads.manage"))]
ClientViewDep = Annotated[object, Depends(exigir_permissao("leads.view"))]
SESSION_COOKIE = "zr_client_session"
# Achado da validação do Portal do Cliente (17/09/2026): login_cliente não
# tinha nenhum limite de tentativas nem bloqueio automático -- diferente do
# login administrativo (auth_routes.limitar_login), permitia força bruta de
# senha sem restrição contra contas de ClientePortal. Mesmo limite usado lá.
_limitar_login_portal = RateLimiter(limite=10, janela_segundos=60, escopo="portal-login")
# Achado médio da Fase 9 (21/09/2026): recuperação de senha do portal não
# tinha nenhum rate limit (diferente de app.api.auth_routes.limitar_recuperacao),
# permitindo mail-bombing de um cliente-alvo em escala. Mesmo limite do admin.
_limitar_recuperacao_portal = RateLimiter(limite=5, janela_segundos=300, escopo="portal-recuperacao")
# Achado médio da auditoria fina do Portal do Cliente (Fase 13.2,
# 23/09/2026): mensagens e uploads eram as únicas mutações autenticadas
# do portal sem nenhum limite de tentativas/frequência (diferente de
# login e recuperação, que já tinham) -- uma credencial comprometida
# podia encher o histórico de mensagens do CRM ou disparar uploads de
# até 15 MB repetidamente, cada um passando pelo scan do clamav, sem
# nenhuma trava. Mesma janela dos outros limites do arquivo.
_limitar_mensagem_portal = RateLimiter(limite=20, janela_segundos=60, escopo="portal-mensagem")
_limitar_upload_portal = RateLimiter(limite=10, janela_segundos=60, escopo="portal-upload")
# Achado médio da auditoria fina do Portal do Cliente (Fase 13.2,
# 23/09/2026, decisão do usuário): assinar proposta/documento no portal
# dependia só da sessão (12h) + CSRF, sem reconfirmação no momento da
# assinatura -- diferente do fluxo público de aceite, que já exige um
# código de 6 dígitos por e-mail (app.api.leads_propostas). Mesmo padrão
# aqui, mesmos limites/janelas do fluxo público.
CODIGO_CONFIRMACAO_PORTAL_MINUTOS = 15
CODIGO_CONFIRMACAO_PORTAL_TENTATIVAS_MAXIMAS = 5
_limitar_codigo_confirmacao_portal = RateLimiter(limite=1, janela_segundos=60, escopo="portal-codigo-confirmacao")


async def _enviar_recuperacao_portal_com_log(
    email: str, nome: str, token: str, cliente_id: int, organizacao_id: int | None = None
) -> None:
    """Rodado como BackgroundTask, depois da resposta já ter sido enviada --
    achado médio da Fase 9 (21/09/2026): aguardar o envio SMTP antes de
    responder criava um oráculo de tempo (a resposta demorava visivelmente
    mais quando a conta existia, mesmo com o corpo da resposta sendo
    idêntico), permitindo enumerar e-mails de clientes com portal ativo. A
    falha continua logada, nunca engolida em silêncio (achado da varredura
    ampla de 18/09/2026).

    Fase 19.3: o nome do escritório (remetente/assunto) é resolvido aqui,
    também depois da resposta, para não reintroduzir diferença de latência
    entre conta existente e inexistente. Falha nessa consulta não impede o
    envio -- só cai na identidade padrão."""
    nome_escritorio = None
    if organizacao_id:
        try:
            async with session_factory() as sessao:
                await aplicar_contexto_tenant(sessao, organizacao_id)
                nome_escritorio = await nome_escritorio_para_email(sessao, organizacao_id)
        except Exception:
            logger.exception("Falha ao resolver o nome do escritório para o e-mail do portal (org %s)", organizacao_id)
    try:
        await enviar_recuperacao_portal(email, nome, token, organizacao_nome=nome_escritorio)
    except Exception:
        logger.exception("Falha ao enviar e-mail de recuperação do portal do cliente %s", cliente_id)


# Item 1 do pedido de melhorias do cliente final do usuário (17/09/2026):
# linha do tempo do processo de registro com % de progresso, pra bater o
# olho e entender em qual etapa está. Baseado em Processo.situacao_normalizada
# (já classificado pelo job app/cli/consolidar_situacoes_marcas.py a partir
# das movimentações reais do INPI). A jornada real do INPI não é linear --
# exigência, oposição, recurso e sobrestamento são desvios condicionais, não
# etapas fixas -- por isso entram como "alerta" na etapa em que normalmente
# ocorrem, sem criar um degrau de progresso à parte nem retroceder o cliente.
# "resultado" só é "negativo" nos desfechos que encerram o processo sem
# registro (ou o extinguem depois de concedido); todo o resto é "ativo".
_ETAPAS_PROCESSO: dict[str | None, dict] = {
    None: {"percentual": 20, "etapa": "Depositado", "alerta": None, "macro": 2},
    "nao_classificada": {"percentual": 20, "etapa": "Depositado", "alerta": None, "macro": 2},
    "publicada": {"percentual": 40, "etapa": "Publicado para oposição", "alerta": None, "macro": 3},
    "oposicao": {
        "percentual": 40,
        "etapa": "Publicado para oposição",
        "alerta": "Marca sob oposição de terceiros",
        "macro": 3,
    },
    "em_exame": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": None, "macro": 4},
    "exigencia": {
        "percentual": 60,
        "etapa": "Em exame de mérito",
        "alerta": "Exigência aberta — aguardando resposta",
        "macro": 4,
    },
    "suspensa": {
        "percentual": 60,
        "etapa": "Em exame de mérito",
        "alerta": "Processo sobrestado (suspenso)",
        "macro": 4,
    },
    "recurso": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": "Em recurso da decisão", "macro": 4},
    "recurso_decidido": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": "Recurso decidido", "macro": 4},
    "peticao_decidida": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": "Petição decidida", "macro": 4},
    "deferida": {"percentual": 80, "etapa": "Deferido", "alerta": None, "macro": 5},
    "deferida_parcial": {
        "percentual": 80,
        "etapa": "Deferido parcialmente",
        "alerta": "Deferimento parcial — nem todas as classes foram concedidas",
        "macro": 5,
    },
    "registrada": {"percentual": 100, "etapa": "Registro concedido", "alerta": None, "macro": 5},
    "indeferida": {
        "percentual": 60,
        "etapa": "Pedido indeferido",
        "alerta": "Pedido indeferido",
        "resultado": "negativo",
        "macro": 4,
    },
    "arquivada": {
        "percentual": 20,
        "etapa": "Processo arquivado",
        "alerta": "Processo arquivado",
        "resultado": "negativo",
        "macro": 2,
    },
    "inexistente": {
        "percentual": 20,
        "etapa": "Pedido considerado inexistente",
        "alerta": "Pedido considerado inexistente",
        "resultado": "negativo",
        "macro": 2,
    },
    "extinta": {
        "percentual": 100,
        "etapa": "Registro extinto",
        "alerta": "Registro extinto",
        "resultado": "negativo",
        "macro": 5,
    },
    "cancelada": {
        "percentual": 100,
        "etapa": "Registro cancelado",
        "alerta": "Registro cancelado",
        "resultado": "negativo",
        "macro": 5,
    },
}


def progresso_processo(situacao_normalizada: str | None) -> dict:
    """Mapeia Processo.situacao_normalizada num progresso amigável pro
    cliente: percentual (múltiplo de 20, pra bater com o padrão de classes
    CSS w-pct-N), etapa (rótulo), alerta (aviso opcional, sem afetar o
    percentual) e resultado ("ativo" ou "negativo")."""
    info = _ETAPAS_PROCESSO.get(situacao_normalizada) or _ETAPAS_PROCESSO["nao_classificada"]
    return {
        "percentual": info["percentual"],
        "etapa": info["etapa"],
        "alerta": info["alerta"],
        "resultado": info.get("resultado", "ativo"),
    }


def _validar_documento_portal_pronto(documento: DocumentoLead) -> None:
    """Levanta 409 com a causa específica se o documento ainda não está
    pronto pra assinatura no portal. Achado da Fase 13.6 (23/09/2026): o
    botão "Assinar" aparecia na tela pra qualquer documento não assinado,
    inclusive um recém-criado ainda "pendente" e sem número/data (ver
    app.api.leads.DOCUMENTOS_VALIDOS) -- o cliente só descobria que não
    dava pra assinar depois de pedir o código por e-mail. Única fonte de
    verdade usada tanto aqui quanto em _documento_portal_pronto_para_assinar
    (exposto em /v1/portal/resumo pro front decidir se mostra o botão)."""
    if documento.status not in DOCUMENTOS_VALIDOS:
        raise HTTPException(status_code=409, detail="Documento ainda não está pronto para assinatura")
    if not documento.numero or not documento.data:
        raise HTTPException(status_code=409, detail="Documento incompleto; aguarde a equipe preencher os dados")
    if documento.validade_em and documento.validade_em < datetime.now(UTC).date():
        raise HTTPException(status_code=409, detail="Documento expirado; solicite uma nova versão")


def _documento_portal_pronto_para_assinar(documento: DocumentoLead) -> bool:
    try:
        _validar_documento_portal_pronto(documento)
    except HTTPException:
        return False
    return True


def _serializar_parcela_portal(parcela: ParcelaFinanceira, descricoes_lancamento: dict[int, str]) -> dict:
    """Serializa uma parcela pro /v1/portal/resumo. Achado P1 da revisão da
    Fase 13.5 (Codex): a coluna persistida fica "aberta" mesmo depois de
    vencida -- igual ao financeiro (app/api/financeiro.py::_serializar), o
    "atrasada" é derivado comparando o vencimento com hoje, não lido do
    banco. Achado P2: sem a descrição do lançamento associado, o cliente
    não conseguia saber a qual proposta/cobrança cada parcela pertencia."""
    return {
        "id": parcela.id,
        "lancamento_id": parcela.lancamento_id,
        "descricao_lancamento": descricoes_lancamento.get(parcela.lancamento_id),
        "numero": parcela.numero,
        "vencimento": parcela.vencimento,
        "valor": parcela.valor,
        "valor_pago": parcela.valor_pago,
        "status": "atrasada" if parcela.status == "aberta" and parcela.vencimento < date.today() else parcela.status,
        "pago_em": parcela.pago_em,
    }


_JORNADA_REGISTRO: tuple[tuple[FaseLead, str], ...] = (
    (FaseLead.CONTATO_INICIAL, "Contato inicial"),
    (FaseLead.QUALIFICADO, "Qualificado"),
    (FaseLead.RELATORIO_ENVIADO, "Relatório enviado"),
    (FaseLead.PROPOSTA_ENVIADA, "Proposta enviada"),
    (FaseLead.PROPOSTA_ACEITA, "Proposta aceita"),
    (FaseLead.AGUARDANDO_PAGAMENTO, "Aguardando pagamento"),
    (FaseLead.PAGAMENTO_CONFIRMADO, "Pagamento confirmado"),
    (FaseLead.GANHO, "Contratação concluída"),
    (FaseLead.PROTOCOLO_INPI, "Protocolo no INPI"),
    (FaseLead.PROCESSO_INPI, "Processo no INPI"),
)


def montar_jornada_registro(
    lead: Lead,
    historico: list[HistoricoFaseLead],
    propostas: list[PropostaComercial],
    processos: list[tuple[ProcessoMonitorado, Processo]],
) -> list[dict]:
    """Monta a jornada comercial e operacional sem fabricar datas.

    Uma mudança manual de fase pode não ter gravado os degraus anteriores.
    Nesse caso eles são apresentados como concluídos sem data registrada,
    nunca como "pulados". Eventos objetivos da proposta e do vínculo com o
    processo complementam o histórico do funil.
    """

    evidencias: dict[str, datetime] = {FaseLead.CONTATO_INICIAL.value: lead.criado_em}
    for evento in historico:
        atual = evidencias.get(evento.fase)
        if atual is None or evento.entrou_em < atual:
            evidencias[evento.fase] = evento.entrou_em

    campos_proposta = (
        (FaseLead.PROPOSTA_ENVIADA.value, "enviado_em"),
        (FaseLead.PROPOSTA_ACEITA.value, "aceito_em"),
        (FaseLead.AGUARDANDO_PAGAMENTO.value, "aceito_em"),
        (FaseLead.PAGAMENTO_CONFIRMADO.value, "pagamento_confirmado_em"),
        (FaseLead.PROTOCOLO_INPI.value, "protocolo_em"),
    )
    for fase, campo in campos_proposta:
        datas = [getattr(proposta, campo) for proposta in propostas if getattr(proposta, campo)]
        if datas:
            evidencias[fase] = min([evidencias[fase], *datas]) if fase in evidencias else min(datas)

    if processos:
        data_vinculo = min(monitorado.criado_em for monitorado, _processo in processos)
        evidencias.setdefault(FaseLead.PROTOCOLO_INPI.value, data_vinculo)
        evidencias.setdefault(FaseLead.PROCESSO_INPI.value, data_vinculo)

    ordem = [fase.value for fase, _label in _JORNADA_REGISTRO]
    indice_atual = ordem.index(lead.fase) if lead.fase in ordem else 0
    jornada: list[dict] = []
    for indice, (fase, label) in enumerate(_JORNADA_REGISTRO):
        ocorrido_em = evidencias.get(fase.value)
        if fase.value == lead.fase:
            situacao = "atual"
        elif ocorrido_em is not None:
            situacao = "concluida"
        elif indice < indice_atual:
            situacao = "concluida_sem_data"
        else:
            situacao = "pendente"
        jornada.append(
            {
                "fase": fase.value,
                "label": label,
                "situacao": situacao,
                "ocorrido_em": ocorrido_em,
            }
        )
    return jornada


MACROETAPAS_LABELS: dict[int, str] = {
    1: "Onboarding & Contratação",
    2: "Protocolo no INPI",
    3: "Publicação & Prazo de Oposição",
    4: "Exame de Mérito",
    5: "Decisão & Emissão de Certificado",
}
PREVISAO_EXAME_MERITO = "Previsão média: 8 a 14 meses"
_FASES_PRE_CONTRATACAO = {fase.value for fase, _label in _JORNADA_REGISTRO[:7]}
_DOC_LABELS: dict[str, str] = {
    "procuracao": "Procuração",
    "gru": "GRU",
    "protocolo": "Comprovante de protocolo",
    "oposicao": "Notificação de oposição",
    "certificado": "Certificado de registro",
}


def _macroetapa_onboarding(jornada_comercial: list[dict], contratacao_concluida: bool) -> dict:
    return {
        "indice": 1,
        "titulo": MACROETAPAS_LABELS[1],
        "situacao": "concluida" if contratacao_concluida else "atual",
        "alerta": None,
        "previsao": None,
        "sub_eventos": [
            {"label": item["label"], "situacao": item["situacao"], "ocorrido_em": item["ocorrido_em"]}
            for item in jornada_comercial[:8]
        ],
    }


def _evento_documento(documento: DocumentoLead | None) -> dict | None:
    if documento is None:
        return None
    return {
        "label": _DOC_LABELS.get(documento.tipo, documento.tipo),
        "situacao": "pendente" if documento.status == "pendente" else "concluida",
        "ocorrido_em": documento.assinado_em or documento.data,
        "documento_id": documento.id if documento.caminho else None,
    }


def _macroetapas_processo(processo: Processo | None, documentos_por_tipo: dict[str, DocumentoLead]) -> list[dict]:
    """Monta as macroetapas 2 a 5 a partir da situação oficial do processo no INPI.

    Sem processo vinculado ainda, as 4 ficam "pendente" -- o cliente só vê a
    macroetapa de onboarding até o protocolo acontecer de fato. Reaproveita
    ``_ETAPAS_PROCESSO`` (mesmo mapa usado por ``progresso_processo``) em vez
    de recriar a classificação de situação -> etapa/alerta.
    """
    if processo is None:
        return [
            {
                "indice": indice,
                "titulo": MACROETAPAS_LABELS[indice],
                "situacao": "pendente",
                "alerta": None,
                "previsao": None,
                "sub_eventos": [],
            }
            for indice in (2, 3, 4, 5)
        ]

    info = _ETAPAS_PROCESSO.get(processo.situacao_normalizada) or _ETAPAS_PROCESSO["nao_classificada"]
    macro_atual = info["macro"]
    # Desfecho encerra a jornada aqui (deferido/registrado positivamente ou
    # indeferido/arquivado/extinto/cancelado negativamente) -- não é mais
    # "em andamento", mesmo sendo a macroetapa mais recente com evidência.
    encerrado = info.get("resultado") == "negativo" or processo.situacao_normalizada == "registrada"

    eventos_por_macro: dict[int, list[dict]] = {2: [], 3: [], 4: [], 5: []}
    if processo.data_deposito:
        eventos_por_macro[2].append(
            {"label": "Pedido depositado", "situacao": "concluida", "ocorrido_em": processo.data_deposito}
        )
    for tipo, macro in (("protocolo", 2), ("oposicao", 3), ("certificado", 5)):
        evento = _evento_documento(documentos_por_tipo.get(tipo))
        if evento:
            eventos_por_macro[macro].append(evento)

    macroetapas = []
    for indice in (2, 3, 4, 5):
        if indice < macro_atual:
            situacao = "concluida"
        elif indice == macro_atual:
            situacao = "concluida" if encerrado else "atual"
        else:
            situacao = "pendente"
        macroetapas.append(
            {
                "indice": indice,
                "titulo": MACROETAPAS_LABELS[indice],
                "situacao": situacao,
                "alerta": info["alerta"] if indice == macro_atual else None,
                "previsao": PREVISAO_EXAME_MERITO if indice == 4 and situacao == "atual" else None,
                "sub_eventos": eventos_por_macro[indice] if situacao != "pendente" else [],
            }
        )
    return macroetapas


def montar_macroetapas(
    lead: Lead,
    jornada_comercial: list[dict],
    processos: list[tuple[ProcessoMonitorado, Processo]],
    documentos: list[DocumentoLead],
) -> list[dict]:
    """Monta a jornada unificada (item 1 do pedido de melhorias, revisão de
    20/09/2026): substitui os 10 passos comerciais lineares + o bloco à
    parte de acompanhamento do INPI por uma jornada única de até 5
    macroetapas por marca/processo, com nós condicionais (exigência,
    oposição, indeferimento) aparecendo só como alerta quando acontecem, em
    vez de virarem um degrau de progresso à parte.

    Documentos objetivos (``TIPOS_DOCUMENTO_LEAD``) só entram como
    sub-evento quando o lead tem exatamente um processo vinculado -- com
    mais de um, não dá pra saber a qual marca o documento pertence
    (``DocumentoLead`` é por lead, não por processo).
    """
    contratacao_concluida = bool(processos) or lead.fase not in _FASES_PRE_CONTRATACAO
    macro1 = _macroetapa_onboarding(jornada_comercial, contratacao_concluida)
    documentos_por_tipo = {documento.tipo: documento for documento in documentos} if len(processos) == 1 else {}

    if not processos:
        return [
            {
                "marca": lead.marca,
                "processo_numero": None,
                "macroetapas": [macro1, *_macroetapas_processo(None, {})],
            }
        ]

    return [
        {
            "marca": processo.titulo or lead.marca,
            "processo_numero": processo.numero,
            "macroetapas": [macro1, *_macroetapas_processo(processo, documentos_por_tipo)],
        }
        for _monitorado, processo in processos
    ]


# Achado médio da Fase 8 (auditoria jurídica, 15/09/2026): o payload bruto
# do webhook Clicksign (nome/e-mail/CPF/telefone/IP do signatário) ficava
# salvo sem redação nenhuma em PropostaComercial.dados["clicksign"][
# "ultimo_evento"] -- dado pessoal do cliente indo parar numa coluna JSON
# de uso operacional (não uma tabela de evidência com controle de acesso
# próprio). Caça por nome de chave em qualquer profundidade do payload,
# em vez de depender do formato exato que o Clicksign manda (schema deles
# não é modelado neste código, e pode variar por tipo de evento).
_CAMPOS_SENSIVEIS_WEBHOOK_CLICKSIGN = {
    "name",
    "nome",
    "full_name",
    "email",
    "e-mail",
    "phone",
    "phone_number",
    "telefone",
    "documentation",
    "cpf",
    "cnpj",
    "birthday",
    "nascimento",
    "ip",
    "ip_address",
    "geolocation",
    "geo",
    "address",
    "endereco",
    "selfie",
}


def _redigir_payload_webhook(valor: object) -> object:
    if isinstance(valor, dict):
        return {
            chave: "[redigido]" if chave.lower() in _CAMPOS_SENSIVEIS_WEBHOOK_CLICKSIGN else _redigir_payload_webhook(sub)
            for chave, sub in valor.items()
        }
    if isinstance(valor, list):
        return [_redigir_payload_webhook(item) for item in valor]
    return valor


@router.post("/v1/webhooks/clicksign")
async def webhook_clicksign(
    request: Request, session: SessionDep, x_clicksign_signature: str | None = Header(default=None)
) -> dict:
    body = await request.body()
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Payload inválido") from exc
    texto = json.dumps(payload, ensure_ascii=False).lower()
    envelope_id = next((str(payload.get(key)) for key in ("envelope_id", "envelopeId") if payload.get(key)), None)
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        envelope_id = envelope_id or str(data.get("id") or data.get("envelope_id") or "") or None
    if not envelope_id:
        raise HTTPException(status_code=422, detail="Envelope não informado")

    # Achados altos da Fase 8 (auditoria jurídica, 15/09/2026): o webhook
    # ficava travado em default_organization_id -- tanto pra resolver o
    # tenant quanto pra escolher o segredo de validação -- então uma
    # organização não-padrão com sua própria conta/segredo do Clicksign
    # (ver app/clicksign.py::configuracao, aceita `org` pra usar
    # credenciais próprias) nunca tinha o pagamento reconhecido por aqui.
    # Não há como saber de antemão qual organização mandou o webhook (a URL
    # é compartilhada por toda a plataforma) -- por isso a busca abaixo roda
    # em modo superadmin (cross-tenant), só pra achar QUAL organização é
    # dona do envelope. É apenas uma consulta, sem mutação nem side-effect;
    # a verificação de assinatura de verdade (com o segredo daquela
    # organização específica) acontece antes de qualquer alteração de dado.
    await aplicar_contexto_tenant(session, get_settings().default_organization_id, superadmin=True)
    proposta = (
        await session.execute(
            select(PropostaComercial)
            .where(PropostaComercial.dados["clicksign"]["envelope_id"].as_string() == envelope_id)
            .limit(1)
            # Serializa com criar_nova_versao_proposta (revisão do Codex no PR #147).
            .with_for_update()
        )
    ).scalar_one_or_none()
    if proposta is None:
        return {"ok": True, "ignorado": True}
    organizacao = await session.get(Organizacao, proposta.organizacao_id)
    config = configuracao_clicksign(organizacao)
    # Achado crítico da Fase 8: sem webhook_secret configurado, os dois
    # `if` abaixo eram pulados por inteiro (fail-open) -- qualquer
    # requisição, de qualquer origem, era aceita como se fosse o Clicksign
    # de verdade, sem assinatura nenhuma. Segredo ausente agora recusa a
    # requisição (fail-closed) em vez de liberar.
    if not config["secret"]:
        logger.error(
            "Webhook Clicksign recusado: webhook_secret não está configurado (organização %s)",
            proposta.organizacao_id,
        )
        raise HTTPException(status_code=401, detail="Webhook não autenticado")
    if not x_clicksign_signature or not hmac.compare_digest(
        (x_clicksign_signature or "").removeprefix("sha256="),
        hmac.new(config["secret"].encode("utf-8"), body, hashlib.sha256).hexdigest(),
    ):
        raise HTTPException(status_code=401, detail="Webhook inválido")
    # Assinatura validada contra o segredo certo -- agora sim volta pro
    # contexto normal (não-superadmin) da organização dona da proposta,
    # antes de qualquer mutação.
    await aplicar_contexto_tenant(session, proposta.organizacao_id)
    if any(term in texto for term in ("document_closed", "envelope_closed", "signed", "assinado", "completed")):
        # Achado 6 do plano proposta-financeiro (Fase 6, 03/09/2026): este era
        # o único dos 3 canais de aceite que não registrava evidência
        # (AssinaturaPropostaComercial) -- gate por ``novo_aceite`` evita criar
        # uma linha por evento (document_closed, envelope_closed, signed...
        # podem chegar em webhooks separados para o mesmo envelope).
        novo_aceite = proposta.aceito_em is None
        # Achado 18.2: o webhook forçava "aceita" em qualquer status -- uma
        # proposta cancelada, recusada, expirada ou substituída por nova
        # versão voltava a valer e gerava contratação/cobrança. Status
        # encerrado prevalece até sobre um aceite anterior (aceita e depois
        # cancelada). A assinatura fica registrada para a equipe decidir,
        # sem efeito financeiro.
        motivo = None if aceite_ja_registrado(proposta) else motivo_bloqueio_aceite(proposta)
        if motivo:
            clicksign_dados = (proposta.dados or {}).get("clicksign") or {}
            # O Clicksign repete o mesmo evento em retry (e manda vários
            # eventos por envelope): registra uma única vez por proposta.
            if not clicksign_dados.get("assinatura_indisponivel_em"):
                logger.warning(
                    "Assinatura Clicksign ignorada para a proposta %s (status %s): %s",
                    proposta.id,
                    proposta.status,
                    motivo,
                )
                registrar_evento_operacional(
                    session,
                    organizacao_id=proposta.organizacao_id,
                    dominio="crm",
                    tipo="crm.assinatura_proposta_indisponivel",
                    entidade_tipo="lead",
                    entidade_id=proposta.lead_id,
                    ator="clicksign",
                    payload={
                        "proposta_id": proposta.id,
                        "status": proposta.status,
                        "envelope_id": envelope_id,
                        "descricao": "Assinatura recebida para proposta que não aceita mais aceite -- nada foi gerado",
                    },
                )
                proposta.dados = {
                    **(proposta.dados or {}),
                    "clicksign": {
                        **clicksign_dados,
                        "assinatura_indisponivel_em": datetime.now(UTC).isoformat(),
                        "ultimo_evento": _redigir_payload_webhook(payload),
                    },
                }
            await session.commit()
            return {"ok": True, "ignorado": True, "proposta_id": proposta.id}
        proposta.status = "aceita"
        proposta.aceito_em = proposta.aceito_em or datetime.now(UTC)
        proposta.public_aceito_em = proposta.public_aceito_em or proposta.aceito_em
        proposta.sla_status = "aguardando_pagamento"
        await criar_contratacao_automatica_proposta(session, proposta, "clicksign")
        if novo_aceite:
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
            # Achado médio da Fase 8 (auditoria jurídica, 15/09/2026): o
            # check "novo_aceite" acima e este INSERT não são atômicos -- o
            # Clicksign reenvia o mesmo webhook em retry, e duas entregas
            # quase simultâneas passavam as duas pelo "aceito_em is None"
            # e criavam duas linhas de evidência pra mesma versão da
            # proposta. Mesmo padrão de begin_nested()/IntegrityError já
            # usado em criar_contratacao_automatica_proposta (PR #48, Fase
            # 7) sobre a mesma classe de corrida -- a proteção de última
            # linha é a UniqueConstraint (proposta_id, versao) da migration
            # d4e5f6a7b8c9.
            try:
                async with session.begin_nested():
                    session.add(
                        AssinaturaPropostaComercial(
                            organizacao_id=proposta.organizacao_id,
                            proposta_id=proposta.id,
                            versao=proposta.versao,
                            hash_documento=assinatura_hash,
                            provedor="clicksign",
                        )
                    )
                    await session.flush()
            except IntegrityError:
                # Outra entrega concorrente do mesmo webhook já registrou a
                # assinatura desta versão -- idempotente por design, não é
                # erro do operador nem do Clicksign.
                pass
    clicksign = (proposta.dados or {}).get("clicksign") or {}
    event_id = (
        payload.get("event_id") or payload.get("eventId") or (data or {}).get("event_id")
        if isinstance(data, dict)
        else None
    )
    if event_id and clicksign.get("ultimo_evento_id") == str(event_id):
        return {"ok": True, "duplicado": True, "proposta_id": proposta.id}
    proposta.dados = {
        **(proposta.dados or {}),
        "clicksign": {
            **clicksign,
            "ultimo_evento": _redigir_payload_webhook(payload),
            "ultimo_evento_id": str(event_id) if event_id else None,
        },
    }
    await session.commit()
    return {"ok": True, "proposta_id": proposta.id}


class ClienteLogin(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=8, max_length=200)


class MensagemInput(BaseModel):
    mensagem: str = Field(min_length=1, max_length=4000)


class MensagemOperadorInput(BaseModel):
    mensagem: str = Field(min_length=1, max_length=4000)


class RecuperacaoSolicitacao(BaseModel):
    email: EmailStr


class RecuperacaoRedefinicao(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    nova_senha: str = Field(min_length=8, max_length=200)


class AssinarComCodigoInput(BaseModel):
    codigo: str = Field(min_length=6, max_length=6)


async def obter_cliente_portal(request: Request, session: SessionDep) -> ClientePortal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Login do cliente necessário")
    sessao = (
        await session.execute(
            select(SessaoClientePortal).where(
                SessaoClientePortal.token_hash == hash_token(token),
                SessaoClientePortal.revogada_em.is_(None),
                SessaoClientePortal.expira_em > datetime.now(UTC),
            )
        )
    ).scalar_one_or_none()
    cliente = (
        (
            await session.execute(
                select(ClientePortal).where(
                    ClientePortal.id == sessao.cliente_id if sessao else False,
                    ClientePortal.ativo.is_(True),
                    ClientePortal.bloqueado_em.is_(None),
                )
            )
        ).scalar_one_or_none()
        if sessao
        else None
    )
    if cliente is None:
        raise HTTPException(status_code=401, detail="Sessão do cliente inválida")
    # Achado P1 do Codex no PR #122 (Fase 13.3, 23/09/2026): revogar
    # sessões ativas no momento da troca de senha é uma corrida (login
    # concorrente com a senha antiga pode validar antes da troca e só
    # comitar a sessão depois). Este marcador de geração fecha a corrida
    # de vez -- comparado a cada requisição, não só no instante da troca --
    # então não importa qual transação comitou primeiro.
    if sessao.senha_versao_no_login != cliente.senha_alterada_em:
        raise HTTPException(status_code=401, detail="Sessão do cliente inválida")
    # Cada requisição do portal cria uma nova sessão de banco. Reaplique o
    # tenant resolvido pelo cookie antes de qualquer auditoria protegida por RLS.
    await aplicar_contexto_tenant(session, cliente.organizacao_id)
    # Guardado pra exigir_csrf_portal conferir sem precisar reconsultar a
    # sessão (ver achado médio da Fase 9: portal não exigia CSRF), e pro
    # logout reaproveitar sem uma segunda query (Fase 13.2, 23/09/2026).
    request.state.portal_csrf_hash = sessao.csrf_hash
    request.state.portal_sessao = sessao
    return cliente


ClientDep = Annotated[ClientePortal, Depends(obter_cliente_portal)]

PORTAL_CSRF_COOKIE = "zr_portal_csrf"
_METODOS_SEGUROS_PORTAL = frozenset({"GET", "HEAD", "OPTIONS"})


async def exigir_csrf_portal(request: Request, cliente: ClientDep) -> ClientePortal:
    """Achado médio da auditoria do Portal do Cliente (Fase 9, 21/09/2026):
    nenhuma mutação exigia CSRF, diferente do painel administrativo
    (app.auth.exigir_csrf). Mesmo padrão de double-submit token aqui:
    cookie legível por JS (definido no login) + header X-CSRF-Token
    conferido contra o hash guardado na sessão. Sessões anteriores a esta
    mudança não têm hash (coluna nullable) -- tratadas como inválidas,
    forçando um novo login (que já gera o par)."""
    if request.method not in _METODOS_SEGUROS_PORTAL:
        token = request.headers.get("X-CSRF-Token", "")
        esperado = getattr(request.state, "portal_csrf_hash", None)
        if not token or not esperado or not secrets.compare_digest(hash_token(token), esperado):
            raise HTTPException(status_code=403, detail="Token CSRF inválido")
    return cliente


ClientCsrfDep = Annotated[ClientePortal, Depends(exigir_csrf_portal)]


def _auditar_cliente(session: AsyncSession, cliente: ClientePortal, request: Request, acao: str, recurso: str) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=cliente.organizacao_id,
            ator=cliente.email,
            acao=acao[:20],
            recurso=recurso[:180],
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(request.client.host if request.client else None),
            detalhes={"cliente_id": cliente.id},
        )
    )


def _auditar_operador(
    session: AsyncSession,
    usuario: object,
    request: Request,
    acao: str,
    recurso: str,
    detalhes: dict | None = None,
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
            ip_hash=hash_ip(request.client.host if request.client else None),
            detalhes=detalhes or {},
        )
    )


def _cliente_dict(cliente: ClientePortal) -> dict:
    return {
        "id": cliente.id,
        "lead_id": cliente.lead_id,
        "nome": cliente.nome,
        "email": cliente.email,
    }


async def _processos_monitorados_do_lead(
    session: AsyncSession, lead_id: int, organizacao_id: int
) -> list[tuple[ProcessoMonitorado, Processo]]:
    """Acha os processos do INPI vinculados a este lead pelo FK real
    (ProcessoMonitorado.lead_id) -- achado "Ruptura 2" da auditoria completa
    do CRM (06/09/2026): antes o portal resolvia por igualdade de string
    entre lead.processo_numero e Processo.numero, que (a) falhava
    silenciosamente -- lista vazia, sem erro -- em qualquer divergência de
    formatação, e (b) só enxergava 1 processo por lead mesmo quando a
    oportunidade tinha várias marcas monitoradas. Não filtra por status:
    o cliente deve ver também processos já concluídos/encerrados."""
    linhas = (
        await session.execute(
            select(ProcessoMonitorado, Processo)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .where(ProcessoMonitorado.lead_id == lead_id, ProcessoMonitorado.organizacao_id == organizacao_id)
            .order_by(ProcessoMonitorado.criado_em)
        )
    ).all()
    return [(monitorado, processo) for monitorado, processo in linhas]


@router.post("/v1/portal/login")
async def login_cliente(dados: ClienteLogin, request: Request, response: Response, session: SessionDep) -> dict:
    _limitar_login_portal.aplicar(cliente_ip(request))
    cliente = (
        await session.execute(select(ClientePortal).where(ClientePortal.email == str(dados.email).lower()))
    ).scalar_one_or_none()
    agora = datetime.now(UTC)
    bloqueado_por_conta = cliente is not None and cliente.bloqueado_ate and cliente.bloqueado_ate > agora
    valido = (
        cliente is not None
        and cliente.ativo
        and not cliente.bloqueado_em
        and not bloqueado_por_conta
        and verificar_senha(cliente.senha_hash, dados.senha)
    )
    if not valido:
        # Achado alto da Fase 9 (21/09/2026): faltava bloqueio da própria
        # conta -- só havia rate-limit por IP, contornável rotacionando IP.
        # Mesmo limiar (5 falhas / 15 min) usado no login administrativo.
        if cliente is not None and not bloqueado_por_conta:
            await aplicar_contexto_tenant(session, cliente.organizacao_id)
            cliente.tentativas_falhas += 1
            if cliente.tentativas_falhas >= 5:
                cliente.bloqueado_ate = agora + timedelta(minutes=15)
                cliente.tentativas_falhas = 0
            await session.commit()
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos")
    # O login do cliente ocorre sem a sessão do operador; estabeleça o tenant
    # antes de gravar a auditoria protegida por RLS.
    await aplicar_contexto_tenant(session, cliente.organizacao_id)
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    session.add(
        SessaoClientePortal(
            cliente_id=cliente.id,
            token_hash=hash_token(token),
            csrf_hash=hash_token(csrf),
            expira_em=datetime.now(UTC) + timedelta(hours=12),
            # Marcador de geração de senha (achado P1 do Codex no PR #122,
            # Fase 13.3) -- ver ClientePortal.senha_alterada_em.
            senha_versao_no_login=cliente.senha_alterada_em,
        )
    )
    cliente.tentativas_falhas = 0
    cliente.bloqueado_ate = None
    cliente.ultimo_login_em = agora
    _auditar_cliente(session, cliente, request, "login_cliente", "portal:login")
    await session.commit()
    comum = {
        "samesite": "lax",
        "secure": requisicao_https(request),
        "max_age": 43200,
        "path": "/",
    }
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **comum)
    response.set_cookie(PORTAL_CSRF_COOKIE, csrf, httponly=False, **comum)
    return {"cliente": _cliente_dict(cliente)}


@router.post("/v1/portal/recuperacao/solicitar")
async def solicitar_recuperacao_portal(
    dados: RecuperacaoSolicitacao, request: Request, session: SessionDep, background_tasks: BackgroundTasks
) -> dict:
    _limitar_recuperacao_portal.aplicar(cliente_ip(request))
    cliente = (
        await session.execute(
            select(ClientePortal).where(ClientePortal.email == str(dados.email).lower(), ClientePortal.ativo.is_(True))
        )
    ).scalar_one_or_none()
    # Resposta indistinguível evita enumeração de clientes.
    if cliente is not None:
        await aplicar_contexto_tenant(session, cliente.organizacao_id)
        token = secrets.token_urlsafe(48)
        session.add(
            RecuperacaoClientePortal(
                cliente_id=cliente.id,
                token_hash=hash_token(token),
                expira_em=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        _auditar_cliente(session, cliente, request, "recuperacao_solicitada", "portal:recuperacao")
        await session.commit()
        # BackgroundTask -- a resposta não espera o SMTP, fechando o
        # oráculo de tempo do achado médio da Fase 9 (o envio só quando a
        # conta existia deixava a latência visivelmente diferente).
        background_tasks.add_task(
            _enviar_recuperacao_portal_com_log, cliente.email, cliente.nome, token, cliente.id, cliente.organizacao_id
        )
    return {"status": "ok", "mensagem": "Se a conta existir, a recuperação foi criada."}


@router.post("/v1/portal/recuperacao/redefinir")
async def redefinir_acesso_portal(
    dados: RecuperacaoRedefinicao, request: Request, response: Response, session: SessionDep
) -> dict:
    _limitar_recuperacao_portal.aplicar(cliente_ip(request))
    agora_token = datetime.now(UTC)
    # Achado 8 da auditoria (07/10/2026): consumo atômico do token (UPDATE
    # condicional + rowcount) -- duas requisições simultâneas com o mesmo token
    # não passam mais as duas.
    consumo = await session.execute(
        update(RecuperacaoClientePortal)
        .where(
            RecuperacaoClientePortal.token_hash == hash_token(dados.token),
            RecuperacaoClientePortal.usado_em.is_(None),
            RecuperacaoClientePortal.expira_em > agora_token,
        )
        .values(usado_em=agora_token)
        .returning(RecuperacaoClientePortal.cliente_id)
    )
    linha = consumo.first()
    if linha is None:
        raise HTTPException(status_code=400, detail="Token de recuperação inválido ou expirado")
    cliente = await session.get(ClientePortal, linha[0])
    if cliente is None or not cliente.ativo:
        raise HTTPException(status_code=400, detail="Conta indisponível")
    await aplicar_contexto_tenant(session, cliente.organizacao_id)
    cliente.senha_hash = hash_senha(dados.nova_senha)
    # Marcador de geração de senha (achado P1 do Codex no PR #122, Fase
    # 13.3) -- é o que fecha de verdade a corrida com um login concorrente
    # usando a senha antiga.
    cliente.senha_alterada_em = datetime.now(UTC)
    for sessao in (
        await session.execute(
            select(SessaoClientePortal).where(
                SessaoClientePortal.cliente_id == cliente.id,
                SessaoClientePortal.revogada_em.is_(None),
            )
        )
    ).scalars():
        sessao.revogada_em = datetime.now(UTC)
    _auditar_cliente(session, cliente, request, "recuperacao_redefinida", "portal:recuperacao")
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    # Mesmo achado de logout_cliente (Fase 13.3, 23/09/2026): o cookie CSRF
    # também precisa ser limpo aqui, não só o de sessão.
    response.delete_cookie(PORTAL_CSRF_COOKIE, path="/")
    return {"status": "ok", "mensagem": "Acesso redefinido. Faça login novamente."}


@router.post("/v1/portal/logout")
async def logout_cliente(request: Request, response: Response, cliente: ClientCsrfDep, session: SessionDep) -> dict:
    """Achado baixo da auditoria fina do Portal do Cliente (Fase 13.2,
    23/09/2026): era a única mutação do arquivo sem exigir CSRF -- o
    cookie de sessão é enviado automaticamente pelo navegador, então um
    site malicioso podia forjar este POST e derrubar a sessão do cliente
    sem interação nenhuma. ClientCsrfDep já resolve sessão/cliente e
    confere o token double-submit antes de chegar aqui; a sessão
    resolvida fica em request.state.portal_sessao (ver obter_cliente_portal),
    sem precisar reconsultar."""
    sessao = request.state.portal_sessao
    # aplicar_contexto_tenant já rodou dentro de ClientCsrfDep -> ClientDep.
    sessao.revogada_em = datetime.now(UTC)
    _auditar_cliente(session, cliente, request, "logout_cliente", "portal:logout")
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    # Achado baixo da auditoria fina do Portal do Cliente (Fase 13.3,
    # 23/09/2026): só o cookie de sessão era apagado -- o cookie CSRF
    # (zr_portal_csrf, legível por JS) ficava órfão no navegador. Não é
    # explorável sozinho (o hash correspondente na sessão já foi
    # revogado acima), mas é higiene de sessão incompleta.
    response.delete_cookie(PORTAL_CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/v1/portal/me")
async def portal_me(request: Request, cliente: ClientDep, session: SessionDep) -> dict:
    _auditar_cliente(session, cliente, request, "consultar_perfil", "portal:me")
    await session.commit()
    return {"cliente": _cliente_dict(cliente)}


@router.post("/v1/admin/leads/{lead_id}/portal-acesso")
async def criar_acesso_cliente(lead_id: int, request: Request, session: SessionDep, usuario: ClientManageDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if lead.responsavel_id != usuario.id and usuario.perfil != "administrador" and not usuario.superadmin:
        raise HTTPException(status_code=403, detail="Somente o responsável pelo atendimento pode gerar este acesso")
    senha_temporaria = secrets.token_urlsafe(10)
    agora = datetime.now(UTC)
    cliente = (
        await session.execute(
            select(ClientePortal).where(
                ClientePortal.lead_id == lead.id,
                ClientePortal.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if cliente is None:
        cliente = ClientePortal(
            organizacao_id=lead.organizacao_id,
            lead_id=lead.id,
            nome=lead.nome,
            email=lead.email.lower(),
            senha_hash=hash_senha(senha_temporaria),
            senha_alterada_em=agora,
            criado_por=usuario.id,
        )
        session.add(cliente)
    else:
        cliente.nome, cliente.email, cliente.senha_hash, cliente.ativo, cliente.bloqueado_em = (
            lead.nome,
            lead.email.lower(),
            hash_senha(senha_temporaria),
            True,
            None,
        )
        # Achado médio da auditoria fina do Portal do Cliente (Fase 13.3,
        # 23/09/2026): ao reemitir acesso pra um cliente já existente, uma
        # sessão antiga ficava válida (cookie ainda dentro do prazo de 12h)
        # mesmo depois da senha trocada -- diferente de
        # redefinir_acesso_portal (autorredefinição), que já revoga. Mesmo
        # motivo de gerar senha nova costuma ser suspeita de conta
        # comprometida; deixar uma sessão antiga viva anularia o propósito.
        # senha_alterada_em (achado P1 do Codex no PR #122) é o que fecha a
        # corrida de verdade -- o SELECT-e-revogar abaixo é só o registro
        # explícito de quais sessões foram cortadas, não protege sozinho
        # contra um login concorrente que comita depois deste SELECT.
        cliente.senha_alterada_em = agora
        for sessao in (
            await session.execute(
                select(SessaoClientePortal).where(
                    SessaoClientePortal.cliente_id == cliente.id,
                    SessaoClientePortal.revogada_em.is_(None),
                )
            )
        ).scalars():
            sessao.revogada_em = agora
    _auditar_operador(
        session,
        usuario,
        request,
        "criar_acesso_portal",
        f"lead:{lead_id}",
        {"cliente_id": cliente.id if cliente.id else None},
    )
    await session.commit()
    return {
        "cliente": _cliente_dict(cliente),
        "senha_temporaria": senha_temporaria,
        "portal": "/portal",
    }


@router.get("/v1/admin/leads/{lead_id}/portal-acesso")
async def consultar_acesso_cliente(lead_id: int, request: Request, session: SessionDep, usuario: ClientViewDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if lead.responsavel_id != usuario.id and usuario.perfil != "administrador" and not usuario.superadmin:
        raise HTTPException(status_code=403, detail="Somente o responsável pelo atendimento pode abrir este acesso")
    cliente = (
        await session.execute(
            select(ClientePortal).where(
                ClientePortal.lead_id == lead_id,
                ClientePortal.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if cliente is None:
        return {"existe": False, "ativo": False}
    _auditar_operador(session, usuario, request, "consultar_acesso_portal", f"cliente:{cliente.id}")
    await session.commit()
    return {
        "existe": True,
        "ativo": cliente.ativo and cliente.bloqueado_em is None,
        "cliente": _cliente_dict(cliente),
        "portal": "/portal",
    }


@router.patch("/v1/admin/portal-clientes/{cliente_id}/acesso")
async def alterar_acesso_cliente(
    cliente_id: int, ativo: bool, request: Request, session: SessionDep, usuario: ClientManageDep
) -> dict:
    cliente = (
        await session.execute(
            select(ClientePortal).where(
                ClientePortal.id == cliente_id,
                ClientePortal.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente do portal não encontrado")
    lead = await session.get(Lead, cliente.lead_id)
    if (
        lead is not None
        and lead.responsavel_id != usuario.id
        and usuario.perfil != "administrador"
        and not usuario.superadmin
    ):
        raise HTTPException(
            status_code=403,
            detail="Somente o responsável pelo atendimento pode alterar este acesso",
        )
    cliente.ativo = ativo
    if not ativo:
        for sessao in (
            await session.execute(
                select(SessaoClientePortal).where(
                    SessaoClientePortal.cliente_id == cliente.id,
                    SessaoClientePortal.revogada_em.is_(None),
                )
            )
        ).scalars():
            sessao.revogada_em = datetime.now(UTC)
    _auditar_operador(
        session,
        usuario,
        request,
        "alterar_acesso_portal",
        f"cliente:{cliente.id}",
        {"ativo": ativo},
    )
    await session.commit()
    return {"ok": True, "ativo": cliente.ativo}


@router.get("/v1/admin/leads/{lead_id}/portal-mensagens")
async def listar_mensagens_portal_admin(lead_id: int, session: SessionDep, usuario: ClientViewDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if lead.responsavel_id != usuario.id and usuario.perfil != "administrador" and not usuario.superadmin:
        raise HTTPException(
            status_code=403,
            detail="Somente o responsável pelo atendimento pode consultar estas mensagens",
        )
    itens = (
        (
            await session.execute(
                select(MensagemClientePortal)
                .where(
                    MensagemClientePortal.lead_id == lead_id,
                    MensagemClientePortal.organizacao_id == usuario.organizacao_id,
                )
                .order_by(MensagemClientePortal.criado_em)
            )
        )
        .scalars()
        .all()
    )
    return {
        "mensagens": [
            {
                "id": item.id,
                "autor_tipo": item.autor_tipo,
                "mensagem": item.mensagem,
                "lida_em": item.lida_em,
                "criado_em": item.criado_em,
            }
            for item in itens
        ]
    }


@router.post("/v1/admin/leads/{lead_id}/portal-mensagens", status_code=status.HTTP_201_CREATED)
async def responder_mensagem_portal(
    lead_id: int,
    dados: MensagemOperadorInput,
    request: Request,
    session: SessionDep,
    usuario: ClientManageDep,
) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if lead.responsavel_id != usuario.id and usuario.perfil != "administrador" and not usuario.superadmin:
        raise HTTPException(status_code=403, detail="Somente o responsável pelo atendimento pode responder")
    cliente = (
        await session.execute(
            select(ClientePortal).where(
                ClientePortal.lead_id == lead_id,
                ClientePortal.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if cliente is None:
        raise HTTPException(status_code=404, detail="Este cliente ainda não possui acesso ao portal")
    item = MensagemClientePortal(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead_id,
        cliente_id=cliente.id,
        autor_tipo="operador",
        autor_id=usuario.id,
        mensagem=dados.mensagem.strip(),
    )
    session.add(item)
    _auditar_operador(session, usuario, request, "responder_portal", f"lead:{lead_id}")
    await session.commit()
    return {"id": item.id}


@router.post("/v1/admin/leads/{lead_id}/portal-mensagens/ler")
async def marcar_mensagens_portal_lidas(lead_id: int, session: SessionDep, usuario: ClientManageDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if lead.responsavel_id != usuario.id and usuario.perfil != "administrador" and not usuario.superadmin:
        raise HTTPException(status_code=403, detail="Somente o responsável pelo atendimento pode marcar mensagens")
    cliente = (
        await session.execute(
            select(ClientePortal).where(
                ClientePortal.lead_id == lead_id,
                ClientePortal.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if cliente is None:
        return {"atualizadas": 0}
    agora = datetime.now(UTC)
    itens = (
        (
            await session.execute(
                select(MensagemClientePortal).where(
                    MensagemClientePortal.cliente_id == cliente.id,
                    MensagemClientePortal.autor_tipo == "cliente",
                    MensagemClientePortal.lida_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for item in itens:
        item.lida_em = agora
    await session.commit()
    return {"atualizadas": len(itens)}


# Achado da validação do Portal do Cliente (17/09/2026): documentos enviados
# pelo cliente (ArquivoClientePortal, POST /v1/portal/arquivos) não tinham
# NENHUM equivalente administrativo -- diferente das mensagens do portal
# (par listar_mensagens_portal_admin/GET .../portal-mensagens acima), a
# equipe não tinha como ver nem baixar o que o cliente enviou. Mesmo padrão
# de permissão (responsável pelo lead, administrador ou superadmin).
@router.get("/v1/admin/leads/{lead_id}/portal-arquivos")
async def listar_arquivos_portal_admin(lead_id: int, session: SessionDep, usuario: ClientViewDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if lead.responsavel_id != usuario.id and usuario.perfil != "administrador" and not usuario.superadmin:
        raise HTTPException(
            status_code=403,
            detail="Somente o responsável pelo atendimento pode consultar estes arquivos",
        )
    itens = (
        (
            await session.execute(
                select(ArquivoClientePortal)
                .where(
                    ArquivoClientePortal.lead_id == lead_id,
                    ArquivoClientePortal.organizacao_id == usuario.organizacao_id,
                )
                .order_by(ArquivoClientePortal.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "arquivos": [
            {
                "id": item.id,
                "nome": item.nome,
                "content_type": item.content_type,
                "tamanho": item.tamanho,
                "hash": item.arquivo_hash,
                "criado_em": item.criado_em,
            }
            for item in itens
        ]
    }


@router.get("/v1/admin/leads/{lead_id}/portal-arquivos/{arquivo_id}/download")
async def baixar_arquivo_portal_admin(
    lead_id: int, arquivo_id: int, request: Request, session: SessionDep, usuario: ClientViewDep
) -> FileResponse:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if lead.responsavel_id != usuario.id and usuario.perfil != "administrador" and not usuario.superadmin:
        raise HTTPException(
            status_code=403,
            detail="Somente o responsável pelo atendimento pode baixar este arquivo",
        )
    item = (
        await session.execute(
            select(ArquivoClientePortal).where(
                ArquivoClientePortal.id == arquivo_id,
                ArquivoClientePortal.lead_id == lead_id,
                ArquivoClientePortal.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    if item.caminho.startswith("s3://"):
        try:
            conteudo = read_bytes(item.caminho)
        except (StorageError, OSError) as exc:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado") from exc
        _auditar_operador(session, usuario, request, "baixar_arquivo_portal", f"arquivo:{item.id}")
        await session.commit()
        return StreamingResponse(
            iter([conteudo]),
            media_type=item.content_type or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{item.nome}"'},
        )
    caminho = Path(item.caminho).resolve()
    base = (local_root() / "portal" / str(usuario.organizacao_id) / str(item.cliente_id)).resolve()
    if not caminho.is_file() or base not in caminho.parents:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    _auditar_operador(session, usuario, request, "baixar_arquivo_portal", f"arquivo:{item.id}")
    await session.commit()
    return FileResponse(caminho, media_type=item.content_type or "application/octet-stream", filename=item.nome)


# Item 2 do pedido de melhorias do cliente final (17/09/2026): área de
# Identidade Visual por cliente. Escopo definido com o usuário: só a equipe
# interna cadastra materiais (logo, manual de marca, artes prontas); o
# cliente só visualiza e baixa no portal -- mesmo padrão de permissão e
# armazenamento de ArquivoClientePortal acima, mas com o fluxo de upload
# invertido (aqui é o operador que envia, o cliente que baixa).
def _checar_acesso_lead_operador(lead: Lead | None, usuario: object) -> None:
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if lead.responsavel_id != usuario.id and usuario.perfil != "administrador" and not usuario.superadmin:
        raise HTTPException(
            status_code=403,
            detail="Somente o responsável pelo atendimento pode gerenciar estes materiais",
        )


@router.get("/v1/admin/leads/{lead_id}/materiais-marca")
async def listar_materiais_marca_admin(lead_id: int, session: SessionDep, usuario: ClientViewDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    _checar_acesso_lead_operador(lead, usuario)
    itens = (
        (
            await session.execute(
                select(MaterialMarcaCliente)
                .where(
                    MaterialMarcaCliente.lead_id == lead_id,
                    MaterialMarcaCliente.organizacao_id == usuario.organizacao_id,
                )
                .order_by(MaterialMarcaCliente.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "materiais": [
            {
                "id": item.id,
                "nome": item.nome,
                "descricao": item.descricao,
                "content_type": item.content_type,
                "tamanho": item.tamanho,
                "criado_em": item.criado_em,
            }
            for item in itens
        ]
    }


@router.post("/v1/admin/leads/{lead_id}/materiais-marca", status_code=status.HTTP_201_CREATED)
async def enviar_material_marca_admin(
    lead_id: int,
    request: Request,
    session: SessionDep,
    usuario: ClientManageDep,
    arquivo: UploadFile = File(...),
    descricao: str | None = Form(None),
) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    _checar_acesso_lead_operador(lead, usuario)
    if arquivo.size and arquivo.size > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo maior que 15 MB")
    nome = f"{secrets.token_hex(12)}-{Path(arquivo.filename or 'arquivo').name}"
    conteudo = await arquivo.read()
    if len(conteudo) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo maior que 15 MB")
    await escanear_upload_ou_rejeitar(conteudo)
    try:
        caminho = save_bytes(f"materiais-marca/{usuario.organizacao_id}/{lead_id}/{nome}", conteudo)
    except StorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    item = MaterialMarcaCliente(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead_id,
        nome=arquivo.filename or nome,
        descricao=(descricao or "").strip() or None,
        caminho=caminho,
        content_type=arquivo.content_type,
        tamanho=len(conteudo),
        arquivo_hash=hashlib.sha256(conteudo).hexdigest(),
        enviado_por_id=usuario.id,
    )
    session.add(item)
    _auditar_operador(session, usuario, request, "enviar_material_marca", f"lead:{lead_id}")
    await session.commit()
    return {"id": item.id, "nome": item.nome, "tamanho": item.tamanho}


@router.get("/v1/admin/leads/{lead_id}/materiais-marca/{material_id}/download")
async def baixar_material_marca_admin(
    lead_id: int, material_id: int, request: Request, session: SessionDep, usuario: ClientViewDep
) -> FileResponse:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    _checar_acesso_lead_operador(lead, usuario)
    item = (
        await session.execute(
            select(MaterialMarcaCliente).where(
                MaterialMarcaCliente.id == material_id,
                MaterialMarcaCliente.lead_id == lead_id,
                MaterialMarcaCliente.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Material não encontrado")
    if item.caminho.startswith("s3://"):
        try:
            conteudo = read_bytes(item.caminho)
        except (StorageError, OSError) as exc:
            raise HTTPException(status_code=404, detail="Material não encontrado") from exc
        _auditar_operador(session, usuario, request, "baixar_material_marca", f"material:{item.id}")
        await session.commit()
        return StreamingResponse(
            iter([conteudo]),
            media_type=item.content_type or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{item.nome}"'},
        )
    caminho = Path(item.caminho).resolve()
    base = (local_root() / "materiais-marca" / str(usuario.organizacao_id) / str(lead_id)).resolve()
    if not caminho.is_file() or base not in caminho.parents:
        raise HTTPException(status_code=404, detail="Material não encontrado")
    _auditar_operador(session, usuario, request, "baixar_material_marca", f"material:{item.id}")
    await session.commit()
    return FileResponse(caminho, media_type=item.content_type or "application/octet-stream", filename=item.nome)


@router.delete("/v1/admin/leads/{lead_id}/materiais-marca/{material_id}")
async def remover_material_marca_admin(
    lead_id: int, material_id: int, request: Request, session: SessionDep, usuario: ClientManageDep
) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    _checar_acesso_lead_operador(lead, usuario)
    item = (
        await session.execute(
            select(MaterialMarcaCliente).where(
                MaterialMarcaCliente.id == material_id,
                MaterialMarcaCliente.lead_id == lead_id,
                MaterialMarcaCliente.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Material não encontrado")
    delete_object(item.caminho)
    await session.delete(item)
    _auditar_operador(session, usuario, request, "remover_material_marca", f"material:{material_id}")
    await session.commit()
    return {"removido": True}


# Item 4/5 do pedido de melhorias do cliente final (17/09/2026): logo do
# próprio cliente exibida dinamicamente na mão do personagem no portal.
# Mesmo padrão de validação/normalização/armazenamento da logo da
# organização (app/api/confiabilidade.py::enviar_logo), mas por lead --
# cada lead tem a própria imagem, guardada em Lead.logo_cliente (mesmo
# formato de Organizacao.branding["logo_asset"]).
def logo_cliente_url(lead: Lead) -> str | None:
    asset = (lead.logo_cliente or {}).get("sha256") if lead.logo_cliente else None
    return f"/v1/portal/logo-cliente?v={asset[:16]}" if asset else None


@router.post("/v1/admin/leads/{lead_id}/logo-cliente", status_code=status.HTTP_201_CREATED)
async def enviar_logo_cliente_admin(
    lead_id: int,
    request: Request,
    session: SessionDep,
    usuario: ClientManageDep,
    arquivo: UploadFile = File(...),
) -> dict:
    from app.api.confiabilidade import normalizar_logo

    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    _checar_acesso_lead_operador(lead, usuario)
    conteudo = await arquivo.read(1024 * 1024 + 1)
    try:
        normalizado, largura, altura = normalizar_logo(conteudo)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    digest = hashlib.sha256(normalizado).hexdigest()
    chave = f"logo-cliente/lead-{lead_id}/{digest}.png"
    try:
        localizacao = save_bytes(chave, normalizado)
    except StorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    asset_anterior = dict(lead.logo_cliente or {})
    lead.logo_cliente = {
        "localizacao": localizacao,
        "sha256": digest,
        "tamanho": len(normalizado),
        "largura": largura,
        "altura": altura,
        "formato": "PNG",
        "atualizado_em": datetime.now(UTC).isoformat(),
        "atualizado_por": usuario.email,
    }
    _auditar_operador(session, usuario, request, "enviar_logo_cliente", f"lead:{lead_id}")
    try:
        await session.commit()
    except Exception:
        delete_object(localizacao)
        raise
    localizacao_anterior = asset_anterior.get("localizacao")
    if localizacao_anterior and localizacao_anterior != localizacao:
        try:
            delete_object(localizacao_anterior)
        except (OSError, StorageError):
            pass
    return {"logo_url": logo_cliente_url(lead), "sha256": digest}


@router.get("/v1/admin/leads/{lead_id}/logo-cliente")
async def baixar_logo_cliente_admin(lead_id: int, session: SessionDep, usuario: ClientViewDep) -> Response:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    _checar_acesso_lead_operador(lead, usuario)
    localizacao = (lead.logo_cliente or {}).get("localizacao")
    if not localizacao:
        raise HTTPException(status_code=404, detail="Logo não configurada")
    try:
        conteudo = read_bytes(localizacao)
    except (OSError, StorageError):
        raise HTTPException(status_code=404, detail="Logo não encontrada") from None
    return Response(
        content=conteudo,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/v1/admin/leads/{lead_id}/logo-cliente")
async def remover_logo_cliente_admin(
    lead_id: int, request: Request, session: SessionDep, usuario: ClientManageDep
) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    _checar_acesso_lead_operador(lead, usuario)
    asset = dict(lead.logo_cliente or {})
    lead.logo_cliente = None
    _auditar_operador(session, usuario, request, "remover_logo_cliente", f"lead:{lead_id}")
    await session.commit()
    localizacao = asset.get("localizacao")
    if localizacao:
        try:
            delete_object(localizacao)
        except (OSError, StorageError):
            pass
    return {"status": "ok"}


@router.get("/v1/portal/logo-cliente")
async def baixar_logo_cliente_portal(cliente: ClientDep, session: SessionDep) -> Response:
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == cliente.lead_id, Lead.organizacao_id == cliente.organizacao_id)
        )
    ).scalar_one()
    asset = lead.logo_cliente or {}
    localizacao = asset.get("localizacao")
    if not localizacao:
        raise HTTPException(status_code=404, detail="Logo não configurada")
    try:
        conteudo = read_bytes(localizacao)
    except (OSError, StorageError):
        raise HTTPException(status_code=404, detail="Logo não encontrada") from None
    return Response(
        content=conteudo,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/v1/portal/resumo")
async def portal_resumo(request: Request, cliente: ClientDep, session: SessionDep) -> dict:
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == cliente.lead_id, Lead.organizacao_id == cliente.organizacao_id)
        )
    ).scalar_one()
    propostas = (
        (
            await session.execute(
                select(PropostaComercial)
                .where(
                    PropostaComercial.lead_id == lead.id,
                    PropostaComercial.organizacao_id == cliente.organizacao_id,
                )
                .order_by(PropostaComercial.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    historico_fases = (
        (
            await session.execute(
                select(HistoricoFaseLead)
                .where(
                    HistoricoFaseLead.lead_id == lead.id,
                    HistoricoFaseLead.organizacao_id == cliente.organizacao_id,
                )
                .order_by(HistoricoFaseLead.entrou_em)
            )
        )
        .scalars()
        .all()
    )
    documentos = (
        (
            await session.execute(
                select(DocumentoLead).where(
                    DocumentoLead.lead_id == lead.id,
                    DocumentoLead.organizacao_id == cliente.organizacao_id,
                )
            )
        )
        .scalars()
        .all()
    )
    guias = (
        (
            await session.execute(
                select(GuiaInpi)
                .where(GuiaInpi.lead_id == lead.id, GuiaInpi.organizacao_id == cliente.organizacao_id)
                .order_by(GuiaInpi.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    pagamentos = (
        (
            await session.execute(
                select(LancamentoFinanceiro)
                .where(
                    LancamentoFinanceiro.lead_id == lead.id,
                    LancamentoFinanceiro.organizacao_id == cliente.organizacao_id,
                )
                .order_by(LancamentoFinanceiro.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    parcelas = (
        (
            await session.execute(
                select(ParcelaFinanceira)
                .join(LancamentoFinanceiro)
                .where(
                    ParcelaFinanceira.organizacao_id == cliente.organizacao_id,
                    LancamentoFinanceiro.lead_id == lead.id,
                )
                .order_by(ParcelaFinanceira.vencimento)
            )
        )
        .scalars()
        .all()
    )
    _lancamentos_por_id = {p.id: p.descricao for p in pagamentos}
    processos_monitorados = await _processos_monitorados_do_lead(session, lead.id, cliente.organizacao_id)
    processos = [
        {
            "numero": processo.numero,
            "titulo": processo.titulo,
            "situacao": processo.situacao,
            **progresso_processo(processo.situacao_normalizada),
        }
        for _monitorado, processo in processos_monitorados
    ]
    _auditar_cliente(session, cliente, request, "consultar_resumo", "portal:resumo")
    await session.commit()
    return {
        "cliente": _cliente_dict(cliente),
        "lead": {
            "id": lead.id,
            "marca": lead.marca,
            "fase": lead.fase,
            "logo_cliente_url": logo_cliente_url(lead),
        },
        "jornadas": montar_macroetapas(
            lead,
            montar_jornada_registro(lead, historico_fases, propostas, processos_monitorados),
            processos_monitorados,
            documentos,
        ),
        "processos": processos,
        "propostas": [
            {
                "id": p.id,
                "numero": p.numero,
                "status": p.status,
                "total": (p.honorarios or 0) + (p.taxa_gru or 0),
                "aceito_em": p.aceito_em,
                "sla_status": p.sla_status,
            }
            for p in propostas
        ],
        "documentos": [
            {
                "id": d.id,
                "tipo": d.tipo,
                "status": d.status,
                "numero": d.numero,
                "versao": d.versao,
                "hash": d.hash_documento,
                "validade_em": d.validade_em,
                "obrigatorio": d.obrigatorio,
                "assinado_em": d.assinado_em,
                "tem_arquivo": bool(d.caminho),
                "pronto_para_assinar": _documento_portal_pronto_para_assinar(d),
            }
            for d in documentos
        ],
        "guias": [
            {
                "id": g.id,
                "descricao": g.descricao,
                "status": g.status,
                "valor": g.valor,
                "vencimento": g.vencimento,
                "numero_gru": g.numero_gru,
                "pago_em": g.pago_em,
            }
            for g in guias
        ],
        "pagamentos": [
            {
                "id": p.id,
                "descricao": p.descricao,
                "tipo": p.tipo,
                "status": p.status,
                "valor": p.valor_total,
                "competencia": p.competencia,
            }
            for p in pagamentos
        ],
        "parcelas": [_serializar_parcela_portal(p, _lancamentos_por_id) for p in parcelas],
    }


def _gerar_codigo_confirmacao_portal() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_assinatura_proposta(proposta: PropostaComercial) -> str:
    return hashlib.sha256(
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
        ).encode()
    ).hexdigest()


def _hash_assinatura_documento(documento: DocumentoLead) -> str:
    """Mesmo hash usado como evidência da assinatura -- reaproveitado
    também como recurso_hash de CodigoConfirmacaoPortal (achado do Codex
    no PR #120): se o operador editar o documento entre o pedido do
    código e a confirmação, o hash muda e o código anterior deixa de
    bater, em vez de continuar valendo pra uma versão diferente da que
    o cliente viu."""
    conteudo = (
        f"{documento.tipo}|{documento.numero or ''}|{documento.data or ''}|"
        f"{documento.observacoes or ''}|v{documento.versao}"
    ).encode()
    return hashlib.sha256(conteudo).hexdigest()


async def _solicitar_codigo_confirmacao_portal(
    session: AsyncSession,
    request: Request,
    cliente: ClientePortal,
    recurso_tipo: str,
    recurso_id: int,
    recurso_hash: str,
    descricao: str,
) -> dict:
    """Gera e envia o código de confirmação (síncrono -- se o e-mail não
    sair, o cliente precisa ver isso na tela agora, não descobrir depois
    que "confirmou" um código que nunca chegou. Mesmo raciocínio do fluxo
    público, ver app.api.leads_propostas.solicitar_codigo_proposta)."""
    _limitar_codigo_confirmacao_portal.aplicar(f"cliente:{cliente.id}:{recurso_tipo}:{recurso_id}")
    codigo = _gerar_codigo_confirmacao_portal()
    codigo_hash = hash_token(codigo)
    expira_em = datetime.now(UTC) + timedelta(minutes=CODIGO_CONFIRMACAO_PORTAL_MINUTOS)
    registro = (
        await session.execute(
            select(CodigoConfirmacaoPortal).where(
                CodigoConfirmacaoPortal.cliente_id == cliente.id,
                CodigoConfirmacaoPortal.recurso_tipo == recurso_tipo,
                CodigoConfirmacaoPortal.recurso_id == recurso_id,
            )
        )
    ).scalar_one_or_none()
    if registro is None:
        registro = CodigoConfirmacaoPortal(
            organizacao_id=cliente.organizacao_id,
            cliente_id=cliente.id,
            recurso_tipo=recurso_tipo,
            recurso_id=recurso_id,
            recurso_hash=recurso_hash,
            codigo_hash=codigo_hash,
            expira_em=expira_em,
        )
        session.add(registro)
    else:
        registro.recurso_hash = recurso_hash
        registro.codigo_hash = codigo_hash
        registro.expira_em = expira_em
    registro.tentativas = 0
    registro.enviado_em = datetime.now(UTC)
    _auditar_cliente(session, cliente, request, "codigo_confirmacao_solicitado", f"{recurso_tipo}:{recurso_id}")
    # Fase 19.3: resolvido antes do commit, ainda no contexto do tenant.
    nome_escritorio = await nome_escritorio_para_email(session, cliente.organizacao_id)
    await session.commit()
    try:
        await enviar_codigo_confirmacao_portal(
            cliente.email, cliente.nome, codigo, descricao, organizacao_nome=nome_escritorio
        )
    except Exception as exc:
        logger.exception("Falha ao enviar código de confirmação do portal do cliente %s", cliente.id)
        raise HTTPException(status_code=502, detail="Não foi possível enviar o código. Tente novamente.") from exc
    return {"status": "ok", "mensagem": "Enviamos um código de confirmação para o seu e-mail cadastrado."}


async def _validar_codigo_confirmacao_portal(
    session: AsyncSession, cliente: ClientePortal, recurso_tipo: str, recurso_id: int, codigo: str, recurso_hash: str
) -> None:
    """Levanta HTTPException se o código não bater -- chamado no início de
    assinar_proposta_portal/assinar_documento_portal, antes de qualquer
    efeito da assinatura. Consome o código (apaga o registro) só quando
    aceito, pra não permitir reuso.

    Dois achados do Codex no PR #120, corrigidos aqui:
    1. FOR UPDATE trava a linha até o fim da transação -- duas
       confirmações quase simultâneas do mesmo código (ex.: duplo clique
       no diálogo, que não desabilita o botão de novo) serializam neste
       SELECT; a segunda só enxerga o registro depois que a primeira já
       comitou (e apagou), então recebe "peça um novo código" em vez de
       conseguir assinar duas vezes -- especialmente importante pra
       documento, que não tem UniqueConstraint de versão como proposta
       (AssinaturaDocumentoLead não bloquearia a segunda assinatura).
    2. recurso_hash compara o conteúdo/versão atual do recurso com o que
       estava vigente quando o código foi pedido -- se o operador editar
       a proposta/documento nos 15 minutos de validade, o código emitido
       pra versão antiga deixa de servir."""
    registro = (
        await session.execute(
            select(CodigoConfirmacaoPortal)
            .where(
                CodigoConfirmacaoPortal.cliente_id == cliente.id,
                CodigoConfirmacaoPortal.recurso_tipo == recurso_tipo,
                CodigoConfirmacaoPortal.recurso_id == recurso_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if registro is None or registro.expira_em < datetime.now(UTC):
        raise HTTPException(status_code=400, detail="Peça um novo código para continuar.")
    if registro.tentativas >= CODIGO_CONFIRMACAO_PORTAL_TENTATIVAS_MAXIMAS:
        raise HTTPException(status_code=400, detail="Muitas tentativas com este código. Peça um novo código.")
    if registro.recurso_hash != recurso_hash:
        raise HTTPException(
            status_code=409, detail="O conteúdo mudou desde que o código foi enviado. Peça um novo código."
        )
    if not secrets.compare_digest(hash_token(codigo.strip()), registro.codigo_hash):
        registro.tentativas += 1
        await session.commit()
        raise HTTPException(status_code=400, detail="Código incorreto. Confira seu e-mail e tente de novo.")
    await session.delete(registro)


@router.post("/v1/portal/propostas/{proposta_id}/assinar/codigo")
async def solicitar_codigo_assinatura_proposta_portal(
    proposta_id: int, request: Request, cliente: ClientCsrfDep, session: SessionDep
) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.lead_id == cliente.lead_id,
                PropostaComercial.organizacao_id == cliente.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    return await _solicitar_codigo_confirmacao_portal(
        session,
        request,
        cliente,
        "proposta",
        proposta.id,
        _hash_assinatura_proposta(proposta),
        f"Proposta {proposta.numero}",
    )


@router.post("/v1/portal/documentos/{documento_id}/assinar/codigo")
async def solicitar_codigo_assinatura_documento_portal(
    documento_id: int, request: Request, cliente: ClientCsrfDep, session: SessionDep
) -> dict:
    documento = (
        await session.execute(
            select(DocumentoLead).where(
                DocumentoLead.id == documento_id,
                DocumentoLead.lead_id == cliente.lead_id,
                DocumentoLead.organizacao_id == cliente.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if documento is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    _validar_documento_portal_pronto(documento)
    return await _solicitar_codigo_confirmacao_portal(
        session,
        request,
        cliente,
        "documento",
        documento.id,
        _hash_assinatura_documento(documento),
        f"Documento {documento.tipo}",
    )


@router.post("/v1/portal/propostas/{proposta_id}/assinar")
async def assinar_proposta_portal(
    proposta_id: int, request: Request, dados: AssinarComCodigoInput, cliente: ClientCsrfDep, session: SessionDep
) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial)
            .where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.lead_id == cliente.lead_id,
                PropostaComercial.organizacao_id == cliente.organizacao_id,
            )
            # Serializa com criar_nova_versao_proposta (revisão do Codex no PR #147).
            .with_for_update()
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    # Mesma regra de aceite dos outros canais (achado 18.2) -- inclui a
    # validade (achado 7 do plano proposta-financeiro) e a versão substituída
    # por uma nova (achado 18.1, cancelada na criação da nova versão). Uma
    # proposta já aceita continua idempotente mesmo depois de vencer, mas
    # status encerrado (ex.: aceita e depois cancelada) prevalece.
    if not aceite_ja_registrado(proposta):
        motivo = motivo_bloqueio_aceite(proposta)
        if motivo:
            raise HTTPException(status_code=409, detail=motivo)
    assinatura_hash = _hash_assinatura_proposta(proposta)
    await _validar_codigo_confirmacao_portal(session, cliente, "proposta", proposta.id, dados.codigo, assinatura_hash)
    agora = datetime.now(UTC)
    ip_hash = hash_ip(request.client.host if request.client else None)
    if proposta.public_aceito_em is None:
        proposta.public_aceito_em = agora
        proposta.aceito_em = agora
        proposta.public_aceito_ip_hash = ip_hash
        proposta.status = "aceita"
        proposta.sla_status = "aguardando_pagamento"
        await criar_contratacao_automatica_proposta(session, proposta, "portal")
        # Achado médio da Fase 8 (mesma corrida do webhook Clicksign, ver
        # comentário em webhook_clicksign): um duplo clique no botão de
        # assinar no portal passava duas requisições quase simultâneas pelo
        # "public_aceito_em is None". Proteção de última linha é a
        # UniqueConstraint (proposta_id, versao) da migration d4e5f6a7b8c9.
        try:
            async with session.begin_nested():
                session.add(
                    AssinaturaPropostaComercial(
                        organizacao_id=cliente.organizacao_id,
                        proposta_id=proposta.id,
                        versao=proposta.versao,
                        hash_documento=assinatura_hash,
                        cliente_id=cliente.id,
                        ip_hash=ip_hash,
                        provedor="portal",
                        # Achado do Codex no PR #120: esta assinatura já passou
                        # por _validar_codigo_confirmacao_portal acima -- mesma
                        # evidência de segundo fator do aceite público
                        # (leads_propostas.py), senão a assinatura no portal
                        # fica indistinguível de uma sem verificação em auditoria.
                        segundo_fator_canal="email",
                        segundo_fator_confirmado_em=agora,
                    )
                )
                await session.flush()
        except IntegrityError:
            pass
    _auditar_cliente(session, cliente, request, "assinar_proposta", f"proposta:{proposta.id}")
    await session.commit()
    return {
        "ok": True,
        "proposta_id": proposta.id,
        "versao": proposta.versao,
        "hash": assinatura_hash,
        "assinado_em": proposta.aceito_em,
    }


@router.post("/v1/portal/documentos/{documento_id}/assinar")
async def assinar_documento_portal(
    documento_id: int, request: Request, dados: AssinarComCodigoInput, cliente: ClientCsrfDep, session: SessionDep
) -> dict:
    documento = (
        await session.execute(
            select(DocumentoLead).where(
                DocumentoLead.id == documento_id,
                DocumentoLead.lead_id == cliente.lead_id,
                DocumentoLead.organizacao_id == cliente.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if documento is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    # Achado médio da Fase 9 (21/09/2026): diferente de assinar_proposta_portal
    # (checa proposta.status antes de aceitar), esta rota não verificava se o
    # documento já tinha sido de fato preenchido pela equipe -- um DocumentoLead
    # recém-criado nasce com status "pendente" e numero/data vazios (ver
    # app/models/_core.py::DocumentoLead), mas já aparecia em /v1/portal/resumo
    # com o documento_id pronto pra assinar. Mesmo conjunto DOCUMENTOS_VALIDOS
    # usado no gate de avanço de fase (app/api/leads.py).
    _validar_documento_portal_pronto(documento)
    digest = _hash_assinatura_documento(documento)
    await _validar_codigo_confirmacao_portal(session, cliente, "documento", documento.id, dados.codigo, digest)
    nova_assinatura = not (documento.assinado_em and documento.hash_documento == digest)
    if documento.assinado_em and documento.hash_documento != digest:
        session.add(
            VersaoDocumentoLead(
                organizacao_id=documento.organizacao_id,
                documento_id=documento.id,
                versao=documento.versao,
                hash_documento=documento.hash_documento or digest,
                conteudo={
                    "tipo": documento.tipo,
                    "numero": documento.numero,
                    "data": documento.data.isoformat() if documento.data else None,
                    "status": documento.status,
                    "observacoes": documento.observacoes,
                },
                criado_por_tipo="cliente",
                criado_por_id=cliente.id,
            )
        )
        documento.versao += 1
        documento.assinado_em = None
    documento.hash_documento = digest
    documento.assinado_em = datetime.now(UTC)
    documento.assinado_ip_hash = hash_ip(request.client.host if request.client else None)
    documento.assinado_por_cliente_id = cliente.id
    if nova_assinatura:
        session.add(
            AssinaturaDocumentoLead(
                organizacao_id=cliente.organizacao_id,
                documento_id=documento.id,
                cliente_id=cliente.id,
                versao=documento.versao,
                hash_documento=digest,
                ip_hash=documento.assinado_ip_hash,
                # Mesma evidência de segundo fator de AssinaturaPropostaComercial
                # -- esta assinatura já passou por _validar_codigo_confirmacao_portal.
                segundo_fator_canal="email",
                segundo_fator_confirmado_em=documento.assinado_em,
            )
        )
    _auditar_cliente(session, cliente, request, "assinar_documento", f"documento:{documento.id}")
    await session.commit()
    return {
        "ok": True,
        "documento_id": documento.id,
        "versao": documento.versao,
        "hash": digest,
        "assinado_em": documento.assinado_em,
    }


@router.get("/v1/portal/processos")
async def listar_processos_portal(request: Request, cliente: ClientDep, session: SessionDep) -> dict:
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == cliente.lead_id, Lead.organizacao_id == cliente.organizacao_id)
        )
    ).scalar_one()
    processos = [
        {
            "id": processo.id,
            "numero": processo.numero,
            "titulo": processo.titulo,
            "situacao": processo.situacao,
            "situacao_normalizada": processo.situacao_normalizada,
            "fonte": processo.fonte,
            **progresso_processo(processo.situacao_normalizada),
        }
        for _monitorado, processo in await _processos_monitorados_do_lead(session, lead.id, cliente.organizacao_id)
    ]
    _auditar_cliente(session, cliente, request, "consultar_processos", "portal:processos")
    await session.commit()
    return {"processos": processos}


STATUS_PRAZO_VISIVEL_CLIENTE = (
    "aguardando_confirmacao",
    "pendente",
    "em_andamento",
    "concluido",
    "cancelado",
    "historico",
    "dispensado",
)


@router.get("/v1/portal/prazos")
async def listar_prazos_portal(request: Request, cliente: ClientDep, session: SessionDep) -> dict:
    """Prazos jurídicos do processo do cliente, com campos limitados ao que é
    seguro mostrar externamente (sem responsável interno, checklist, trilha de
    confirmação ou auditoria).

    Achado 5.4 da auditoria (02/09/2026), Fase 9: a tela de login do portal
    promete "Processos e prazos" e "próximos passos", mas o portal nunca teve
    nenhuma conexão com PrazoJuridico — o cliente não tinha como saber o que
    estava pendente. Exclui apenas "duplicado" (artefato interno de
    reconciliação de importação, sem significado para o cliente).
    """
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == cliente.lead_id, Lead.organizacao_id == cliente.organizacao_id)
        )
    ).scalar_one()
    prazos: list[dict] = []
    monitorados = [
        monitorado.id for monitorado, _processo in await _processos_monitorados_do_lead(session, lead.id, cliente.organizacao_id)
    ]
    if monitorados:
        linhas = (
            (
                await session.execute(
                    select(PrazoJuridico)
                    .where(
                        PrazoJuridico.processo_monitorado_id.in_(monitorados),
                        PrazoJuridico.organizacao_id == cliente.organizacao_id,
                        PrazoJuridico.status.in_(STATUS_PRAZO_VISIVEL_CLIENTE),
                    )
                    .order_by(PrazoJuridico.vencimento_em)
                )
            )
            .scalars()
            .all()
        )
        prazos = [
            {
                "id": prazo.id,
                "tipo": prazo.tipo,
                "tipo_descricao": TIPOS_PRAZO.get(prazo.tipo, prazo.tipo),
                "titulo": prazo.titulo,
                "status": prazo.status,
                "prioridade": prazo.prioridade,
                "vencimento_em": prazo.vencimento_em,
                "concluido_em": prazo.concluido_em,
            }
            for prazo in linhas
        ]
    _auditar_cliente(session, cliente, request, "consultar_prazos", "portal:prazos")
    await session.commit()
    return {"prazos": prazos}


@router.get("/v1/portal/documentos/{documento_id}/download", response_model=None)
async def baixar_documento_portal(
    documento_id: int, request: Request, cliente: ClientDep, session: SessionDep
) -> FileResponse | StreamingResponse:
    """Corrigido em 21/09/2026: este endpoint sempre 404ava -- lia
    ``documento.caminho``, um atributo que não existia no modelo até
    DocumentoLead ganhar upload de arquivo de verdade (achado do usuário:
    "Etapa bloqueada" sem lugar pra anexar a procuração)."""
    documento = (
        await session.execute(
            select(DocumentoLead).where(
                DocumentoLead.id == documento_id,
                DocumentoLead.lead_id == cliente.lead_id,
                DocumentoLead.organizacao_id == cliente.organizacao_id,
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
        # Achado do usuário (21/09/2026): o filename do Content-Disposition
        # não tinha extensão -- o navegador salvava "procuracao" sem sufixo
        # e o sistema não sabia com o que abrir. A extensão original
        # sobrevive no nome salvo (ver "nome" em enviar_arquivo_documento_lead).
        # Achado do Codex review (PR #95): montar o header à mão só aceita
        # latin-1 -- um sufixo com caractere fora de ASCII quebrava o
        # download com 500 (UnicodeEncodeError). Filtra pra alfanumérico/ponto.
        extensao = "".join(c for c in Path(documento.caminho).suffix if c.isascii() and (c.isalnum() or c == "."))[:10]
        if len(extensao) <= 1:  # só um "." sobrou depois de filtrar (ex.: extensão só com acentos/CJK)
            extensao = ""
        nome_arquivo = f"{documento.tipo}{extensao}"
        _auditar_cliente(session, cliente, request, "baixar_documento", f"documento:{documento.id}")
        await session.commit()
        return StreamingResponse(
            iter([conteudo]),
            media_type=documento.content_type or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
        )
    caminho = Path(documento.caminho).resolve()
    base = (
        local_root() / "documentos-lead" / str(cliente.organizacao_id) / str(cliente.lead_id) / documento.tipo
    ).resolve()
    if not caminho.is_file() or base not in caminho.parents:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    _auditar_cliente(session, cliente, request, "baixar_documento", f"documento:{documento.id}")
    await session.commit()
    return FileResponse(
        caminho,
        media_type=documento.content_type or "application/octet-stream",
        filename=f"{documento.tipo}{caminho.suffix}",
    )


@router.get("/v1/portal/mensagens")
async def portal_mensagens(request: Request, cliente: ClientDep, session: SessionDep) -> dict:
    itens = (
        (
            await session.execute(
                select(MensagemClientePortal)
                .where(MensagemClientePortal.cliente_id == cliente.id)
                .order_by(MensagemClientePortal.criado_em)
            )
        )
        .scalars()
        .all()
    )
    _auditar_cliente(session, cliente, request, "consultar_mensagens", "portal:mensagens")
    await session.commit()
    return {
        "mensagens": [
            {
                "id": i.id,
                "autor_tipo": i.autor_tipo,
                "mensagem": i.mensagem,
                "criado_em": i.criado_em,
            }
            for i in itens
        ]
    }


@router.post("/v1/portal/mensagens", status_code=status.HTTP_201_CREATED)
async def enviar_mensagem_portal(
    dados: MensagemInput, request: Request, cliente: ClientCsrfDep, session: SessionDep
) -> dict:
    _limitar_mensagem_portal.aplicar(f"cliente:{cliente.id}")
    item = MensagemClientePortal(
        organizacao_id=cliente.organizacao_id,
        lead_id=cliente.lead_id,
        cliente_id=cliente.id,
        mensagem=dados.mensagem.strip(),
    )
    session.add(item)
    _auditar_cliente(session, cliente, request, "mensagem_cliente", f"lead:{cliente.lead_id}")
    await session.commit()
    return {"id": item.id}


@router.post("/v1/portal/arquivos", status_code=status.HTTP_201_CREATED)
async def enviar_arquivo_portal(
    request: Request, cliente: ClientCsrfDep, session: SessionDep, arquivo: UploadFile = File(...)
) -> dict:
    _limitar_upload_portal.aplicar(f"cliente:{cliente.id}")
    if arquivo.size and arquivo.size > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo maior que 15 MB")
    nome = f"{secrets.token_hex(12)}-{Path(arquivo.filename or 'arquivo').name}"
    conteudo = await arquivo.read()
    if len(conteudo) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo maior que 15 MB")
    await escanear_upload_ou_rejeitar(conteudo)
    try:
        caminho = save_bytes(f"portal/{cliente.organizacao_id}/{cliente.id}/{nome}", conteudo)
    except StorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    arquivo_hash = hashlib.sha256(conteudo).hexdigest()
    item = ArquivoClientePortal(
        organizacao_id=cliente.organizacao_id,
        lead_id=cliente.lead_id,
        cliente_id=cliente.id,
        nome=arquivo.filename or nome,
        caminho=caminho,
        content_type=arquivo.content_type,
        tamanho=len(conteudo),
        arquivo_hash=arquivo_hash,
    )
    session.add(item)
    _auditar_cliente(session, cliente, request, "upload_cliente", f"arquivo:{item.nome}")
    await session.commit()
    return {"id": item.id, "nome": item.nome, "tamanho": item.tamanho, "hash": item.arquivo_hash}


@router.get("/v1/portal/arquivos")
async def listar_arquivos_portal(request: Request, cliente: ClientDep, session: SessionDep) -> dict:
    itens = (
        (
            await session.execute(
                select(ArquivoClientePortal)
                .where(
                    ArquivoClientePortal.cliente_id == cliente.id,
                    ArquivoClientePortal.lead_id == cliente.lead_id,
                    ArquivoClientePortal.organizacao_id == cliente.organizacao_id,
                )
                .order_by(ArquivoClientePortal.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    _auditar_cliente(session, cliente, request, "consultar_arquivos", "portal:arquivos")
    await session.commit()
    return {
        "arquivos": [
            {
                "id": item.id,
                "nome": item.nome,
                "content_type": item.content_type,
                "tamanho": item.tamanho,
                "hash": item.arquivo_hash,
                "criado_em": item.criado_em,
            }
            for item in itens
        ]
    }


@router.get("/v1/portal/arquivos/{arquivo_id}/download")
async def baixar_arquivo_portal(
    arquivo_id: int, request: Request, cliente: ClientDep, session: SessionDep
) -> FileResponse:
    item = (
        await session.execute(
            select(ArquivoClientePortal).where(
                ArquivoClientePortal.id == arquivo_id,
                ArquivoClientePortal.cliente_id == cliente.id,
                ArquivoClientePortal.lead_id == cliente.lead_id,
                ArquivoClientePortal.organizacao_id == cliente.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    if item.caminho.startswith("s3://"):
        try:
            conteudo = read_bytes(item.caminho)
        except (StorageError, OSError) as exc:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado") from exc
        _auditar_cliente(session, cliente, request, "baixar_arquivo", f"arquivo:{item.id}")
        await session.commit()
        return StreamingResponse(
            iter([conteudo]),
            media_type=item.content_type or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{item.nome}"'},
        )
    caminho = Path(item.caminho).resolve()
    base = (local_root() / "portal" / str(cliente.organizacao_id) / str(cliente.id)).resolve()
    if not caminho.is_file() or base not in caminho.parents:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    _auditar_cliente(session, cliente, request, "baixar_arquivo", f"arquivo:{item.id}")
    await session.commit()
    return FileResponse(caminho, media_type=item.content_type or "application/octet-stream", filename=item.nome)


@router.get("/v1/portal/materiais-marca")
async def listar_materiais_marca_portal(request: Request, cliente: ClientDep, session: SessionDep) -> dict:
    itens = (
        (
            await session.execute(
                select(MaterialMarcaCliente)
                .where(
                    MaterialMarcaCliente.lead_id == cliente.lead_id,
                    MaterialMarcaCliente.organizacao_id == cliente.organizacao_id,
                )
                .order_by(MaterialMarcaCliente.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    _auditar_cliente(session, cliente, request, "consultar_materiais_marca", "portal:materiais-marca")
    await session.commit()
    return {
        "materiais": [
            {
                "id": item.id,
                "nome": item.nome,
                "descricao": item.descricao,
                "content_type": item.content_type,
                "tamanho": item.tamanho,
                "criado_em": item.criado_em,
            }
            for item in itens
        ]
    }


@router.get("/v1/portal/materiais-marca/{material_id}/download")
async def baixar_material_marca_portal(
    material_id: int, request: Request, cliente: ClientDep, session: SessionDep
) -> FileResponse:
    item = (
        await session.execute(
            select(MaterialMarcaCliente).where(
                MaterialMarcaCliente.id == material_id,
                MaterialMarcaCliente.lead_id == cliente.lead_id,
                MaterialMarcaCliente.organizacao_id == cliente.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Material não encontrado")
    if item.caminho.startswith("s3://"):
        try:
            conteudo = read_bytes(item.caminho)
        except (StorageError, OSError) as exc:
            raise HTTPException(status_code=404, detail="Material não encontrado") from exc
        _auditar_cliente(session, cliente, request, "baixar_material_marca", f"material:{item.id}")
        await session.commit()
        return StreamingResponse(
            iter([conteudo]),
            media_type=item.content_type or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{item.nome}"'},
        )
    caminho = Path(item.caminho).resolve()
    base = (local_root() / "materiais-marca" / str(cliente.organizacao_id) / str(cliente.lead_id)).resolve()
    if not caminho.is_file() or base not in caminho.parents:
        raise HTTPException(status_code=404, detail="Material não encontrado")
    _auditar_cliente(session, cliente, request, "baixar_material_marca", f"material:{item.id}")
    await session.commit()
    return FileResponse(caminho, media_type=item.content_type or "application/octet-stream", filename=item.nome)


# Achado baixo da Fase 13.6 (23/09/2026): /v1/portal/notificacoes,
# /v1/portal/notificacoes/{id}/ler e /v1/portal/eventos, junto com o modelo
# NotificacaoClientePortal, nunca foram consumidos por nenhuma tela --
# nenhum código em app.web nunca gerava uma notificação de cliente nem
# chamava esses endpoints. Removidos; ver migrations/versions para o drop
# da tabela notificacoes_clientes_portal.


@router.get("/portal", include_in_schema=False)
async def pagina_portal() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parent.parent / "web" / "portal-cliente.html")

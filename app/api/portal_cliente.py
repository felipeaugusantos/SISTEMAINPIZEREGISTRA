import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.juridico import TIPOS_PRAZO
from app.api.leads_propostas import criar_contratacao_automatica_proposta
from app.auth import exigir_permissao, hash_ip, hash_senha, hash_token, verificar_senha
from app.clicksign import configuracao as configuracao_clicksign
from app.database import get_session
from app.emailing import enviar_recuperacao_portal
from app.malware_scan import escanear_upload_ou_rejeitar
from app.models import (
    ArquivoClientePortal,
    AssinaturaDocumentoLead,
    AssinaturaPropostaComercial,
    ClientePortal,
    DocumentoLead,
    EventoAuditoria,
    FaseLead,
    GuiaInpi,
    HistoricoFaseLead,
    LancamentoFinanceiro,
    Lead,
    MaterialMarcaCliente,
    MensagemClientePortal,
    NotificacaoClientePortal,
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
from app.storage import StorageError, delete_object, read_bytes, save_bytes
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
    None: {"percentual": 20, "etapa": "Depositado", "alerta": None},
    "nao_classificada": {"percentual": 20, "etapa": "Depositado", "alerta": None},
    "publicada": {"percentual": 40, "etapa": "Publicado para oposição", "alerta": None},
    "oposicao": {"percentual": 40, "etapa": "Publicado para oposição", "alerta": "Marca sob oposição de terceiros"},
    "em_exame": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": None},
    "exigencia": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": "Exigência aberta — aguardando resposta"},
    "suspensa": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": "Processo sobrestado (suspenso)"},
    "recurso": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": "Em recurso da decisão"},
    "recurso_decidido": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": "Recurso decidido"},
    "peticao_decidida": {"percentual": 60, "etapa": "Em exame de mérito", "alerta": "Petição decidida"},
    "deferida": {"percentual": 80, "etapa": "Deferido", "alerta": None},
    "deferida_parcial": {
        "percentual": 80,
        "etapa": "Deferido parcialmente",
        "alerta": "Deferimento parcial — nem todas as classes foram concedidas",
    },
    "registrada": {"percentual": 100, "etapa": "Registro concedido", "alerta": None},
    "indeferida": {"percentual": 60, "etapa": "Pedido indeferido", "alerta": None, "resultado": "negativo"},
    "arquivada": {"percentual": 20, "etapa": "Processo arquivado", "alerta": None, "resultado": "negativo"},
    "inexistente": {
        "percentual": 20,
        "etapa": "Pedido considerado inexistente",
        "alerta": None,
        "resultado": "negativo",
    },
    "extinta": {"percentual": 100, "etapa": "Registro extinto", "alerta": None, "resultado": "negativo"},
    "cancelada": {"percentual": 100, "etapa": "Registro cancelado", "alerta": None, "resultado": "negativo"},
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
            session.add(
                AssinaturaPropostaComercial(
                    organizacao_id=proposta.organizacao_id,
                    proposta_id=proposta.id,
                    versao=proposta.versao,
                    hash_documento=assinatura_hash,
                    provedor="clicksign",
                )
            )
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
            "ultimo_evento": payload,
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
    # Cada requisição do portal cria uma nova sessão de banco. Reaplique o
    # tenant resolvido pelo cookie antes de qualquer auditoria protegida por RLS.
    await aplicar_contexto_tenant(session, cliente.organizacao_id)
    return cliente


ClientDep = Annotated[ClientePortal, Depends(obter_cliente_portal)]


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
    if (
        cliente is None
        or not cliente.ativo
        or cliente.bloqueado_em
        or not verificar_senha(cliente.senha_hash, dados.senha)
    ):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos")
    # O login do cliente ocorre sem a sessão do operador; estabeleça o tenant
    # antes de gravar a auditoria protegida por RLS.
    await aplicar_contexto_tenant(session, cliente.organizacao_id)
    token = secrets.token_urlsafe(48)
    session.add(
        SessaoClientePortal(
            cliente_id=cliente.id,
            token_hash=hash_token(token),
            expira_em=datetime.now(UTC) + timedelta(hours=12),
        )
    )
    cliente.ultimo_login_em = datetime.now(UTC)
    _auditar_cliente(session, cliente, request, "login_cliente", "portal:login")
    await session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=requisicao_https(request),
        max_age=43200,
        path="/",
    )
    return {"cliente": _cliente_dict(cliente)}


@router.post("/v1/portal/recuperacao/solicitar")
async def solicitar_recuperacao_portal(dados: RecuperacaoSolicitacao, request: Request, session: SessionDep) -> dict:
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
        try:
            await enviar_recuperacao_portal(cliente.email, cliente.nome, token)
        except Exception:
            # Não revelar existência da conta nem transformar falha de SMTP
            # em vazamento -- mas a falha precisa ficar visível pra equipe
            # (achado da varredura ampla do sistema, 18/09/2026: antes era
            # engolida sem log nenhum, invisível em produção).
            logger.exception("Falha ao enviar e-mail de recuperação do portal do cliente %s", cliente.id)
    return {"status": "ok", "mensagem": "Se a conta existir, a recuperação foi criada."}


@router.post("/v1/portal/recuperacao/redefinir")
async def redefinir_acesso_portal(
    dados: RecuperacaoRedefinicao, request: Request, response: Response, session: SessionDep
) -> dict:
    registro = (
        await session.execute(
            select(RecuperacaoClientePortal).where(
                RecuperacaoClientePortal.token_hash == hash_token(dados.token),
                RecuperacaoClientePortal.usado_em.is_(None),
                RecuperacaoClientePortal.expira_em > datetime.now(UTC),
            )
        )
    ).scalar_one_or_none()
    if registro is None:
        raise HTTPException(status_code=400, detail="Token de recuperação inválido ou expirado")
    cliente = await session.get(ClientePortal, registro.cliente_id)
    if cliente is None or not cliente.ativo:
        raise HTTPException(status_code=400, detail="Conta indisponível")
    await aplicar_contexto_tenant(session, cliente.organizacao_id)
    cliente.senha_hash = hash_senha(dados.nova_senha)
    registro.usado_em = datetime.now(UTC)
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
    return {"status": "ok", "mensagem": "Acesso redefinido. Faça login novamente."}


@router.post("/v1/portal/logout")
async def logout_cliente(request: Request, response: Response, session: SessionDep) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sessao = (
            await session.execute(
                select(SessaoClientePortal).where(SessaoClientePortal.token_hash == hash_token(token))
            )
        ).scalar_one_or_none()
        if sessao:
            cliente = await session.get(ClientePortal, sessao.cliente_id)
            if cliente is not None:
                # Aplicar o tenant executa um SELECT set_config. Se a sessão
                # for alterada antes, o autoflush tenta fazer o UPDATE ainda
                # no contexto de bootstrap (somente leitura) e o RLS devolve
                # zero linhas, causando StaleDataError. Resolva e aplique o
                # tenant primeiro; só então revogue a sessão.
                await aplicar_contexto_tenant(session, cliente.organizacao_id)
                sessao.revogada_em = datetime.now(UTC)
                _auditar_cliente(session, cliente, request, "logout_cliente", "portal:logout")
                await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
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
    base = (Path("data") / "portal" / str(usuario.organizacao_id) / str(item.cliente_id)).resolve()
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
    base = (Path("data") / "materiais-marca" / str(usuario.organizacao_id) / str(lead_id)).resolve()
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
        "jornada": montar_jornada_registro(lead, historico_fases, propostas, processos_monitorados),
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
        "parcelas": [
            {
                "id": p.id,
                "lancamento_id": p.lancamento_id,
                "numero": p.numero,
                "vencimento": p.vencimento,
                "valor": p.valor,
                "valor_pago": p.valor_pago,
                "status": p.status,
                "pago_em": p.pago_em,
            }
            for p in parcelas
        ],
    }


@router.post("/v1/portal/propostas/{proposta_id}/assinar")
async def assinar_proposta_portal(proposta_id: int, request: Request, cliente: ClientDep, session: SessionDep) -> dict:
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
    if proposta.status not in {"enviada", "visualizada", "aceita"}:
        raise HTTPException(status_code=409, detail="Proposta indisponível para assinatura")
    # Achado 7 do plano proposta-financeiro (Fase 1, 03/09/2026): validade_em
    # nunca era checada. Uma proposta já aceita continua idempotente mesmo
    # depois de vencer (não desfaz um aceite já registrado).
    if (
        proposta.public_aceito_em is None
        and proposta.validade_em
        and proposta.validade_em < datetime.now(UTC).date()
    ):
        raise HTTPException(
            status_code=409, detail="Proposta expirada. Solicite uma nova versão à sua equipe de atendimento."
        )
    agora = datetime.now(UTC)
    ip_hash = hash_ip(request.client.host if request.client else None)
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
        ).encode()
    ).hexdigest()
    if proposta.public_aceito_em is None:
        proposta.public_aceito_em = agora
        proposta.aceito_em = agora
        proposta.public_aceito_ip_hash = ip_hash
        proposta.status = "aceita"
        proposta.sla_status = "aguardando_pagamento"
        await criar_contratacao_automatica_proposta(session, proposta, "portal")
        session.add(
            AssinaturaPropostaComercial(
                organizacao_id=cliente.organizacao_id,
                proposta_id=proposta.id,
                versao=proposta.versao,
                hash_documento=assinatura_hash,
                cliente_id=cliente.id,
                ip_hash=ip_hash,
                provedor="portal",
            )
        )
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
    documento_id: int, request: Request, cliente: ClientDep, session: SessionDep
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
    if documento.validade_em and documento.validade_em < datetime.now(UTC).date():
        raise HTTPException(status_code=409, detail="Documento expirado; solicite uma nova versão")
    conteudo = f"{documento.tipo}|{documento.numero or ''}|{documento.data or ''}|{documento.observacoes or ''}|v{documento.versao}".encode()
    digest = hashlib.sha256(conteudo).hexdigest()
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


@router.get("/v1/portal/documentos/{documento_id}/download")
async def baixar_documento_portal(
    documento_id: int, request: Request, cliente: ClientDep, session: SessionDep
) -> FileResponse:
    documento = (
        await session.execute(
            select(DocumentoLead).where(
                DocumentoLead.id == documento_id,
                DocumentoLead.lead_id == cliente.lead_id,
                DocumentoLead.organizacao_id == cliente.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    caminho_registrado = getattr(documento, "caminho", None) if documento else None
    if documento is None or not caminho_registrado:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    caminho = Path(caminho_registrado).resolve()
    if not caminho.is_file():
        raise HTTPException(status_code=404, detail="Arquivo do documento não encontrado")
    _auditar_cliente(session, cliente, request, "baixar_documento", f"documento:{documento.id}")
    await session.commit()
    return FileResponse(
        caminho,
        media_type="application/octet-stream",
        filename=getattr(documento, "nome", None) or caminho.name,
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
    dados: MensagemInput, request: Request, cliente: ClientDep, session: SessionDep
) -> dict:
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
    request: Request, cliente: ClientDep, session: SessionDep, arquivo: UploadFile = File(...)
) -> dict:
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
    base = (Path("data") / "portal" / str(cliente.organizacao_id) / str(cliente.id)).resolve()
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
    base = (Path("data") / "materiais-marca" / str(cliente.organizacao_id) / str(cliente.lead_id)).resolve()
    if not caminho.is_file() or base not in caminho.parents:
        raise HTTPException(status_code=404, detail="Material não encontrado")
    _auditar_cliente(session, cliente, request, "baixar_material_marca", f"material:{item.id}")
    await session.commit()
    return FileResponse(caminho, media_type=item.content_type or "application/octet-stream", filename=item.nome)


@router.get("/v1/portal/notificacoes")
async def listar_notificacoes_portal(request: Request, cliente: ClientDep, session: SessionDep) -> dict:
    itens = (
        (
            await session.execute(
                select(NotificacaoClientePortal)
                .where(NotificacaoClientePortal.cliente_id == cliente.id)
                .order_by(NotificacaoClientePortal.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    _auditar_cliente(session, cliente, request, "consultar_notificacoes", "portal:notificacoes")
    await session.commit()
    return {
        "notificacoes": [
            {
                "id": item.id,
                "titulo": item.titulo,
                "mensagem": item.mensagem,
                "lida_em": item.lida_em,
                "criado_em": item.criado_em,
            }
            for item in itens
        ]
    }


@router.patch("/v1/portal/notificacoes/{notificacao_id}/ler")
async def marcar_notificacao_lida(
    notificacao_id: int, request: Request, cliente: ClientDep, session: SessionDep
) -> dict:
    item = (
        await session.execute(
            select(NotificacaoClientePortal).where(
                NotificacaoClientePortal.id == notificacao_id,
                NotificacaoClientePortal.cliente_id == cliente.id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Notificação não encontrada")
    item.lida_em = datetime.now(UTC)
    _auditar_cliente(session, cliente, request, "marcar_notificacao", f"notificacao:{item.id}")
    await session.commit()
    return {"ok": True, "lida_em": item.lida_em}


@router.get("/v1/portal/eventos")
async def listar_eventos_portal(request: Request, cliente: ClientDep, session: SessionDep) -> dict:
    itens = (
        (
            await session.execute(
                select(EventoAuditoria)
                .where(
                    EventoAuditoria.organizacao_id == cliente.organizacao_id,
                    EventoAuditoria.detalhes["cliente_id"].as_integer() == cliente.id,
                )
                .order_by(EventoAuditoria.criado_em.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    _auditar_cliente(session, cliente, request, "consultar_eventos", "portal:eventos")
    await session.commit()
    return {
        "eventos": [
            {
                "id": item.id,
                "acao": item.acao,
                "recurso": item.recurso,
                "sucesso": item.sucesso,
                "criado_em": item.criado_em,
            }
            for item in itens
        ]
    }


@router.get("/portal", include_in_schema=False)
async def pagina_portal() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parent.parent / "web" / "portal-cliente.html")

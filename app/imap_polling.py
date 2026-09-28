"""Detecção de resposta por e-mail (Fase 9 do plano Leads/CRM, achado L6) e
histórico bidirecional (achado item 21 da auditoria completa do CRM,
06/09/2026): guarda o conteúdo de cada resposta, não só o timestamp.

Fica inerte enquanto settings.imap_enabled=False (padrão) -- não há caixa de
e-mail dedicada configurada até que credenciais reais sejam fornecidas.
"""

import asyncio
import email
import imaplib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cadencia_email import pausar_envios_pendentes_do_lead
from app.models import Lead, RespostaEmailLead
from app.settings import get_settings

logger = logging.getLogger("ze_registra.imap_polling")

TAMANHO_MAXIMO_CORPO = 5000


@dataclass(frozen=True, slots=True)
class MensagemRecebida:
    endereco: str
    assunto: str | None = None
    corpo: str = ""
    recebido_em: datetime = field(default_factory=lambda: datetime.now(UTC))


def _extrair_corpo(mensagem: Message) -> str:
    """Prefere text/plain; cai para text/html (tags removidas, best-effort)
    quando o remetente só mandou HTML. Trunca -- isto é histórico de CRM,
    não um arquivo de e-mail completo."""
    texto_plano: str | None = None
    texto_html: str | None = None
    if mensagem.is_multipart():
        partes = mensagem.walk()
    else:
        partes = [mensagem]
    for parte in partes:
        if parte.get_content_maintype() == "multipart" or parte.get_filename():
            continue
        try:
            conteudo = parte.get_payload(decode=True)
        except Exception:
            continue
        if conteudo is None:
            continue
        charset = parte.get_content_charset() or "utf-8"
        try:
            texto = conteudo.decode(charset, errors="replace")
        except (LookupError, ValueError):
            texto = conteudo.decode("utf-8", errors="replace")
        if parte.get_content_type() == "text/plain" and texto_plano is None:
            texto_plano = texto
        elif parte.get_content_type() == "text/html" and texto_html is None:
            texto_html = texto
    if texto_plano is not None:
        corpo = texto_plano
    elif texto_html is not None:
        corpo = re.sub(r"<[^>]+>", " ", texto_html)
    else:
        corpo = ""
    corpo = " ".join(corpo.split())
    return corpo[:TAMANHO_MAXIMO_CORPO]


def _buscar_mensagens_nao_lidas(host: str, port: int, username: str, password: str, usar_ssl: bool) -> list[MensagemRecebida]:
    """Bloqueante -- chamar sempre via asyncio.to_thread. Marca as mensagens
    lidas como efeito colateral do FETCH (evita reprocessar na próxima
    varredura)."""
    conexao_cls = imaplib.IMAP4_SSL if usar_ssl else imaplib.IMAP4
    mensagens: list[MensagemRecebida] = []
    with conexao_cls(host, port) as imap:
        imap.login(username, password)
        imap.select("INBOX")
        status, dados = imap.search(None, "UNSEEN")
        if status != "OK" or not dados or not dados[0]:
            return mensagens
        for numero in dados[0].split():
            status, msg_dados = imap.fetch(numero, "(RFC822)")
            if status != "OK" or not msg_dados or not msg_dados[0]:
                continue
            mensagem = email.message_from_bytes(msg_dados[0][1])
            _, endereco = parseaddr(mensagem.get("From", ""))
            if not endereco:
                continue
            try:
                recebido_em = parsedate_to_datetime(mensagem.get("Date", ""))
                if recebido_em.tzinfo is None:
                    recebido_em = recebido_em.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                recebido_em = datetime.now(UTC)
            mensagens.append(
                MensagemRecebida(
                    endereco=endereco.strip().lower(),
                    assunto=(mensagem.get("Subject") or "").strip()[:255] or None,
                    corpo=_extrair_corpo(mensagem),
                    recebido_em=recebido_em,
                )
            )
    return mensagens


async def verificar_respostas_email(session: AsyncSession) -> dict:
    """Job periódico do worker: verifica a caixa dedicada por respostas novas,
    guarda o conteúdo (RespostaEmailLead) e pausa a cadência dos leads
    correspondentes (qualquer organização -- o worker já roda com contexto
    de superadmin)."""
    settings = get_settings()
    if not settings.imap_enabled:
        return {"verificado": False, "motivo": "imap_desabilitado"}
    try:
        mensagens = await asyncio.to_thread(
            _buscar_mensagens_nao_lidas,
            settings.imap_host,
            settings.imap_port,
            settings.imap_username,
            settings.imap_password,
            settings.imap_ssl,
        )
    except Exception as exc:
        logger.warning("Falha ao consultar IMAP: %s", exc)
        return {"verificado": False, "erro": str(exc)}

    pausados = 0
    registradas = 0
    # Achado 17.4: quebra por organização para o worker gravar um
    # AlertaSistema por tenant, em vez do total agregado fixo na org 1.
    pausados_por_organizacao: dict[int, int] = {}
    for mensagem in mensagens:
        leads = (
            await session.execute(
                select(Lead.id, Lead.organizacao_id).where(
                    func.lower(Lead.email) == mensagem.endereco,
                    Lead.anonimizado_em.is_(None),
                )
            )
        ).all()
        for lead_id, organizacao_id in leads:
            session.add(
                RespostaEmailLead(
                    organizacao_id=organizacao_id,
                    lead_id=lead_id,
                    remetente=mensagem.endereco,
                    assunto=mensagem.assunto,
                    corpo=mensagem.corpo,
                    recebido_em=mensagem.recebido_em,
                )
            )
            registradas += 1
            pausados_lead = await pausar_envios_pendentes_do_lead(session, organizacao_id, lead_id)
            pausados += pausados_lead
            if pausados_lead:
                pausados_por_organizacao[organizacao_id] = pausados_por_organizacao.get(organizacao_id, 0) + pausados_lead
    return {
        "verificado": True,
        "remetentes": len({m.endereco for m in mensagens}),
        "registradas": registradas,
        "pausados": pausados,
        "pausados_por_organizacao": pausados_por_organizacao,
    }

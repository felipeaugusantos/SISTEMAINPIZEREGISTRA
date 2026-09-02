"""Detecção de resposta por e-mail (Fase 9 do plano Leads/CRM, achado L6).

Fica inerte enquanto settings.imap_enabled=False (padrão) -- não há caixa de
e-mail dedicada configurada até que credenciais reais sejam fornecidas.
"""

import asyncio
import email
import imaplib
import logging
from email.utils import parseaddr

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cadencia_email import pausar_envios_pendentes_do_lead
from app.models import Lead
from app.settings import get_settings

logger = logging.getLogger("ze_registra.imap_polling")


def _buscar_remetentes_nao_lidos(host: str, port: int, username: str, password: str, usar_ssl: bool) -> list[str]:
    """Bloqueante -- chamar sempre via asyncio.to_thread. Marca as mensagens
    lidas como efeito colateral do FETCH (evita reprocessar na próxima
    varredura)."""
    conexao_cls = imaplib.IMAP4_SSL if usar_ssl else imaplib.IMAP4
    remetentes: list[str] = []
    with conexao_cls(host, port) as imap:
        imap.login(username, password)
        imap.select("INBOX")
        status, dados = imap.search(None, "UNSEEN")
        if status != "OK" or not dados or not dados[0]:
            return remetentes
        for numero in dados[0].split():
            status, msg_dados = imap.fetch(numero, "(RFC822)")
            if status != "OK" or not msg_dados or not msg_dados[0]:
                continue
            mensagem = email.message_from_bytes(msg_dados[0][1])
            _, endereco = parseaddr(mensagem.get("From", ""))
            if endereco:
                remetentes.append(endereco.strip().lower())
    return remetentes


async def verificar_respostas_email(session: AsyncSession) -> dict:
    """Job periódico do worker: verifica a caixa dedicada por respostas novas e
    pausa a cadência dos leads correspondentes (qualquer organização -- o
    worker já roda com contexto de superadmin)."""
    settings = get_settings()
    if not settings.imap_enabled:
        return {"verificado": False, "motivo": "imap_desabilitado"}
    try:
        remetentes = await asyncio.to_thread(
            _buscar_remetentes_nao_lidos,
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
    for endereco in set(remetentes):
        leads = (
            await session.execute(
                select(Lead.id, Lead.organizacao_id).where(
                    func.lower(Lead.email) == endereco,
                    Lead.anonimizado_em.is_(None),
                )
            )
        ).all()
        for lead_id, organizacao_id in leads:
            pausados += await pausar_envios_pendentes_do_lead(session, organizacao_id, lead_id)
    return {"verificado": True, "remetentes": len(set(remetentes)), "pausados": pausados}

"""Webhook do gateway OpenWA: respostas "SAIR", recibos de entrega e alertas da sessão do WhatsApp.

Público (o gateway não tem sessão do painel), por isso fecha em dois cadeados: sem ``OPENWA_WEBHOOK_SECRET``
configurado nada é aceito, e toda entrega precisa da assinatura HMAC-SHA256 sobre os bytes exatos do corpo.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.mensageria import FINALIDADES_DA_SESSAO, processar_evento_gateway
from app.openwa import assinatura_valida
from app.proxy import cliente_ip
from app.ratelimit import RateLimiter
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

router = APIRouter(tags=["mensageria"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
_limitar_webhook = RateLimiter(limite=1200, janela_segundos=60, escopo="openwa-webhook")


@router.post("/v1/webhooks/openwa")
async def webhook_openwa(
    request: Request,
    session: SessionDep,
    x_openwa_signature: str | None = Header(default=None),
    x_zr_sessao: str | None = Header(default=None),
) -> dict:
    settings = get_settings()
    if not settings.openwa_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook do WhatsApp não configurado")
    _limitar_webhook.aplicar(cliente_ip(request))
    corpo = await request.body()
    if not assinatura_valida(corpo, x_openwa_signature, settings.openwa_webhook_secret):
        raise HTTPException(status_code=401, detail="Assinatura inválida")
    try:
        carga = json.loads(corpo)
    except json.JSONDecodeError as erro:
        raise HTTPException(status_code=400, detail="Payload inválido") from erro
    if not isinstance(carga, dict):
        raise HTTPException(status_code=400, detail="Payload inválido")
    dados = carga.get("data")
    organizacao_id = settings.default_organization_id
    # Contexto de plataforma: recibos e pedidos de saída podem pertencer a qualquer organização (o gateway é um só).
    await aplicar_contexto_tenant(session, organizacao_id, superadmin=True)
    acao = await processar_evento_gateway(
        session,
        organizacao_id=organizacao_id,
        evento=str(carga.get("event") or ""),
        dados=dados if isinstance(dados, dict) else {},
        # O comando de configuração registra o webhook de cada sessão com este cabeçalho próprio.
        sessao_logica=x_zr_sessao if x_zr_sessao in FINALIDADES_DA_SESSAO else None,
    )
    await session.commit()
    return {"ok": True, "acao": acao}

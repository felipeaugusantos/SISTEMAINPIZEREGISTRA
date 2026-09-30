"""CAPTCHA da consulta pública (Cloudflare Turnstile).

Pendência da Fase 12 (auditoria da consulta pública, 22/09/2026): o único
mecanismo antibot do formulário público era o campo-armadilha ("website")
mais o limite por IP. Decisão do usuário (29/09/2026): Cloudflare Turnstile,
exigido no envio de dados que cria lead (POST /v1/leads) e no disparo de
uma nova pesquisa de marca (POST /v1/pesquisas-marca). Ver o relatório já
gerado continua livre.

Desligado enquanto as duas chaves (TURNSTILE_SITE_KEY/TURNSTILE_SECRET_KEY)
não estiverem configuradas -- o deploy não quebra antes da conta existir.
Configurado, falha fechada: sem token, token inválido ou Cloudflare
indisponível, o envio é recusado.
"""

import logging

import httpx
from fastapi import HTTPException, Request, status

from app.proxy import cliente_ip
from app.settings import get_settings

logger = logging.getLogger("ze_registra.captcha")

URL_VERIFICACAO_TURNSTILE = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
CABECALHO_TOKEN = "X-Captcha-Token"


def captcha_ativo() -> bool:
    settings = get_settings()
    return bool(settings.turnstile_site_key and settings.turnstile_secret_key)


async def verificar_token_turnstile(token: str, ip: str | None) -> bool:
    """Consulta a Cloudflare. Qualquer falha de rede conta como não verificado."""
    settings = get_settings()
    dados = {"secret": settings.turnstile_secret_key, "response": token}
    if ip:
        dados["remoteip"] = ip
    try:
        async with httpx.AsyncClient(timeout=settings.turnstile_timeout_seconds) as cliente:
            resposta = await cliente.post(URL_VERIFICACAO_TURNSTILE, data=dados)
        resposta.raise_for_status()
        return bool(resposta.json().get("success"))
    except Exception:
        logger.exception("Falha ao verificar o CAPTCHA na Cloudflare")
        return False


async def exigir_captcha(request: Request) -> None:
    """Dependência dos endpoints públicos que criam lead ou disparam pesquisa."""
    if not captcha_ativo():
        return
    token = (request.headers.get(CABECALHO_TOKEN) or "").strip()
    if not token or len(token) > 2048:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Confirme que você não é um robô para continuar.")
    if not await verificar_token_turnstile(token, cliente_ip(request)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Não foi possível confirmar que você não é um robô. Tente novamente.",
        )

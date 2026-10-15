"""Prepara o gateway OpenWA: cria as duas sessões (vigilância e prospecção) e registra os webhooks.

Idempotente: o que já existe é mantido (os webhooks existentes são atualizados com o segredo e os eventos atuais). Não liga o envio (``WHATSAPP_ENABLED``) nem lê o QR code: o QR é
escaneado no painel do gateway, por túnel SSH (ver docs/whatsapp-openwa-runbook.md).

Uso, na VPS, a partir de /opt/zeregistra:
    docker compose exec -T api /app/.venv/bin/python -m app.cli.openwa_configurar
"""

import asyncio
import sys

import httpx

from app.settings import get_settings

URL_WEBHOOK_PADRAO = "http://api:8000/v1/webhooks/openwa"
EVENTOS = ["message.received", "message.ack", "message.failed", "session.restriction", "session.disconnected", "session.reconnect_loop"]
# Estados em que a sessão não está rodando e precisa ser iniciada para mostrar o QR code.
ESTADOS_PARADOS = frozenset({"created", "stopped", "disconnected", "failed"})


async def configurar(
    cliente: httpx.AsyncClient, sessoes: dict[str, str], segredo: str, url_webhook: str = URL_WEBHOOK_PADRAO
) -> list[str]:
    """Cria o que falta e devolve o relato do que foi feito. ``sessoes`` mapeia nome lógico -> nome no gateway."""
    relato: list[str] = []
    resposta = await cliente.get("/api/sessions")
    resposta.raise_for_status()
    existentes = {sessao["name"]: sessao for sessao in resposta.json()}
    for logico, nome in sessoes.items():
        sessao = existentes.get(nome)
        if sessao is None:
            criada = await cliente.post("/api/sessions", json={"name": nome})
            criada.raise_for_status()
            sessao = criada.json()
            relato.append(f"Sessão '{nome}' criada.")
        else:
            relato.append(f"Sessão '{nome}' já existia.")
        if sessao.get("status") in ESTADOS_PARADOS:
            iniciada = await cliente.post(f"/api/sessions/{sessao['id']}/start")
            iniciada.raise_for_status()
            relato.append(f"Sessão '{nome}' iniciada: escaneie o QR code no painel do gateway.")
        listados = await cliente.get(f"/api/sessions/{sessao['id']}/webhooks")
        listados.raise_for_status()
        desejado = {
            "url": url_webhook,
            "events": EVENTOS,
            "secret": segredo,
            "headers": {"X-Zr-Sessao": logico},
            "retryCount": 3,
        }
        existente = next((item for item in listados.json() if item.get("url") == url_webhook), None)
        if existente is not None:
            # O gateway nunca devolve o segredo, então não dá para comparar: atualiza sempre. Assim a troca do
            # OPENWA_WEBHOOK_SECRET (ou dos eventos) chega ao gateway sem recriar o webhook à mão.
            atualizado = await cliente.put(f"/api/sessions/{sessao['id']}/webhooks/{existente['id']}", json=desejado)
            atualizado.raise_for_status()
            relato.append(f"Webhook da sessão '{nome}' atualizado.")
            continue
        criado = await cliente.post(f"/api/sessions/{sessao['id']}/webhooks", json=desejado)
        criado.raise_for_status()
        relato.append(f"Webhook da sessão '{nome}' registrado.")
    return relato


async def executar() -> int:
    settings = get_settings()
    if not (settings.openwa_base_url and settings.openwa_api_key):
        print("Defina OPENWA_BASE_URL e OPENWA_API_KEY.", file=sys.stderr)
        return 1
    if len(settings.openwa_webhook_secret) < 16:
        print("Defina OPENWA_WEBHOOK_SECRET com pelo menos 16 caracteres (use: openssl rand -hex 24).", file=sys.stderr)
        return 1
    sessoes = {"vigilancia": settings.openwa_sessao_vigilancia, "prospeccao": settings.openwa_sessao_prospeccao}
    async with httpx.AsyncClient(
        base_url=settings.openwa_base_url.rstrip("/"),
        headers={"X-API-Key": settings.openwa_api_key},
        timeout=settings.openwa_timeout_segundos,
    ) as cliente:
        try:
            for linha in await configurar(cliente, sessoes, settings.openwa_webhook_secret):
                print(linha)
        except httpx.HTTPError as erro:
            print(f"Falha ao falar com o gateway: {erro}", file=sys.stderr)
            return 1
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(executar()))


if __name__ == "__main__":
    main()

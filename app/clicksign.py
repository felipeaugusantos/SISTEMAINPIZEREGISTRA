"""Cliente mínimo da API Clicksign v3 (Envelope)."""
import base64
from typing import Any

import httpx

from app.security_ext import revelar_segredo
from app.settings import get_settings


def configuracao(org: Any | None = None) -> dict[str, Any]:
    settings = get_settings()
    saved = ((getattr(org, "branding", None) or {}).get("clicksign") or {}) if org else {}
    token = settings.clicksign_api_token
    secret = settings.clicksign_webhook_secret
    try:
        if saved.get("api_token_enc"):
            token = revelar_segredo(saved["api_token_enc"])
        if saved.get("webhook_secret_enc"):
            secret = revelar_segredo(saved["webhook_secret_enc"])
    except ValueError:
        token, secret = "", ""
    base = saved.get("base_url") or settings.clicksign_base_url
    return {"enabled": saved.get("habilitado", settings.clicksign_enabled), "base_url": base.rstrip("/"), "token": token, "secret": secret}


async def criar_envelope(pdf: bytes, nome: str, email: str, nome_signatario: str, org: Any | None = None) -> dict[str, str]:
    config = configuracao(org)
    if not config["enabled"] or not config["token"]:
        raise RuntimeError("Clicksign não está configurada ou habilitada")
    headers = {"Authorization": config["token"], "Content-Type": "application/vnd.api+json", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        def data(response: httpx.Response) -> dict:
            response.raise_for_status()
            return response.json().get("data", {})
        envelope = data(await client.post(f"{config['base_url']}/envelopes", headers=headers, json={"data": {"type": "envelopes", "attributes": {"name": nome}}}))
        envelope_id = envelope["id"]
        document = data(await client.post(f"{config['base_url']}/envelopes/{envelope_id}/documents", headers=headers, json={"data": {"type": "documents", "attributes": {"filename": nome, "content_base64": "data:application/pdf;base64," + base64.b64encode(pdf).decode()}}}))
        signer = data(await client.post(f"{config['base_url']}/envelopes/{envelope_id}/signers", headers=headers, json={"data": {"type": "signers", "attributes": {"name": nome_signatario, "email": email}}}))
        relationships = {"document": {"data": {"type": "documents", "id": document["id"]}}, "signer": {"data": {"type": "signers", "id": signer["id"]}}}
        (await client.post(f"{config['base_url']}/envelopes/{envelope_id}/requirements", headers=headers, json={"data": {"type": "requirements", "attributes": {"action": "agree", "role": "sign"}, "relationships": relationships}})).raise_for_status()
        (await client.post(f"{config['base_url']}/envelopes/{envelope_id}/requirements", headers=headers, json={"data": {"type": "requirements", "attributes": {"action": "provide_evidence", "auth": "email"}, "relationships": relationships}})).raise_for_status()
        (await client.patch(f"{config['base_url']}/envelopes/{envelope_id}", headers=headers, json={"data": {"id": envelope_id, "type": "envelopes", "attributes": {"status": "running"}}})).raise_for_status()
        (await client.post(f"{config['base_url']}/envelopes/{envelope_id}/notifications", headers=headers, json={"data": {"type": "notifications", "attributes": {"channel": "email", "event": "signature_request"}, "relationships": {"signer": {"data": {"type": "signers", "id": signer["id"]}}}}})).raise_for_status()
    return {"envelope_id": envelope_id, "document_id": document["id"], "signer_id": signer["id"]}

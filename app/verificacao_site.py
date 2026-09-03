"""Verificação de presença digital -- Fase 3 do Radar de Prospecção
(03/09/2026, docs/arquitetura-radar-prospeccao-2026-09-03.md).

Fonte escolhida pelo usuário: só checar se o site do prospect responde, sem
provedor pago. Não descobre nada que o prospect não trouxe -- sem redes
sociais, sem site quando o campo `site` está vazio (ativo=None nesse caso,
que é diferente de "site fora do ar").
"""

import httpx

TIMEOUT_SEGUNDOS = 10
USER_AGENT = "INPI-API/0.1 (radar de prospeccao -- verificacao de site)"


def normalizar_url(site: str | None) -> str | None:
    site = (site or "").strip()
    if not site:
        return None
    if not site.lower().startswith(("http://", "https://")):
        site = f"https://{site}"
    return site


async def verificar_site(site: str | None, *, cliente: httpx.AsyncClient | None = None) -> dict:
    """Devolve {"ativo", "status_code", "url_final", "erro"}.

    ativo=None quando não há site pra checar (sem dado, não "fora do ar").
    """
    url = normalizar_url(site)
    if url is None:
        return {"ativo": None, "status_code": None, "url_final": None, "erro": None}

    cliente_proprio = cliente is None
    if cliente_proprio:
        cliente = httpx.AsyncClient(
            timeout=TIMEOUT_SEGUNDOS, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
    try:
        resposta = await cliente.head(url)
        if resposta.status_code >= 405:  # nem todo servidor aceita HEAD
            resposta = await cliente.get(url)
    except httpx.HTTPError as exc:
        return {"ativo": False, "status_code": None, "url_final": None, "erro": type(exc).__name__}
    finally:
        if cliente_proprio:
            await cliente.aclose()

    return {
        "ativo": resposta.status_code < 400,
        "status_code": resposta.status_code,
        "url_final": str(resposta.url),
        "erro": None,
    }

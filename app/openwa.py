"""Cliente do gateway OpenWA (https://github.com/rmyndharis/OpenWA) para o WhatsApp.

Só fala com o gateway que roda na rede interna do projeto (``OPENWA_BASE_URL``); nunca com a internet. Envia
texto por ``POST /api/sessions/{id}/messages/send-text`` com ``Idempotency-Key`` (o gateway devolve o mesmo
resultado se a chave se repetir) e confere a assinatura HMAC dos webhooks.
"""

import hashlib
import hmac
import logging

import httpx

from app.mensageria import ErroEnvioPermanente
from app.settings import get_settings

logger = logging.getLogger("ze_registra.openwa")

# 4xx que não adiantam repetir: número inexistente no WhatsApp, chave sem permissão, corpo inválido.
STATUS_PERMANENTES = frozenset({400, 401, 403, 404, 422})


class ProvedorOpenWA:
    """Implementa ``ProvedorMensagens`` sobre a API REST do OpenWA."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        sessoes: dict[str, str],
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._cabecalhos = {"X-API-Key": api_key}
        self._sessoes = sessoes  # nome lógico (vigilancia/prospeccao) -> nome da sessão no gateway
        self._timeout = timeout
        self._transport = transport
        self._ids: dict[str, str] = {}  # nome da sessão no gateway -> id (uuid) que a API pede na URL

    def _cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, headers=self._cabecalhos, timeout=self._timeout, transport=self._transport
        )

    async def _id_da_sessao(self, cliente: httpx.AsyncClient, nome: str, *, renovar: bool = False) -> str:
        if not renovar and nome in self._ids:
            return self._ids[nome]
        resposta = await cliente.get("/api/sessions")
        resposta.raise_for_status()
        for sessao in resposta.json():
            if sessao.get("name") == nome:
                self._ids[nome] = str(sessao["id"])
                return self._ids[nome]
        raise ErroEnvioPermanente(f"Sessão '{nome}' não existe no gateway; rode app.cli.openwa_configurar")

    async def enviar_texto(self, sessao: str, telefone: str, texto: str, chave_idempotencia: str) -> str:
        nome = self._sessoes.get(sessao, sessao)
        async with self._cliente() as cliente:
            for tentativa in (0, 1):
                id_sessao = await self._id_da_sessao(cliente, nome, renovar=tentativa == 1)
                resposta = await cliente.post(
                    f"/api/sessions/{id_sessao}/messages/send-text",
                    json={"chatId": f"{telefone}@c.us", "text": texto},
                    headers={"Idempotency-Key": chave_idempotencia},
                )
                if resposta.status_code == 404 and tentativa == 0:
                    self._ids.pop(nome, None)  # sessão recriada no gateway: o id mudou
                    continue
                break
        if resposta.status_code in (200, 201):
            return str(resposta.json()["messageId"])
        detalhe = _detalhe(resposta)
        if resposta.status_code in STATUS_PERMANENTES:
            raise ErroEnvioPermanente(f"Gateway recusou ({resposta.status_code}): {detalhe}")
        # 409 (sessão sem conexão), 5xx e demais: temporário, a fila tenta de novo.
        raise RuntimeError(f"Gateway indisponível ({resposta.status_code}): {detalhe}")


def _detalhe(resposta: httpx.Response) -> str:
    try:
        corpo = resposta.json()
        return str(corpo.get("message") or corpo.get("error") or corpo)[:200]
    except ValueError:
        return resposta.text[:200]


def criar_provedor() -> ProvedorOpenWA | None:
    """Provedor configurado, ou None (sem URL ou sem chave, nada é enviado)."""
    settings = get_settings()
    if not (settings.openwa_base_url and settings.openwa_api_key):
        return None
    return ProvedorOpenWA(
        settings.openwa_base_url,
        settings.openwa_api_key,
        {"vigilancia": settings.openwa_sessao_vigilancia, "prospeccao": settings.openwa_sessao_prospeccao},
        timeout=settings.openwa_timeout_segundos,
    )


def assinatura_valida(corpo: bytes, cabecalho: str | None, segredo: str) -> bool:
    """Confere ``X-OpenWA-Signature: sha256=<hex>`` sobre os bytes exatos do corpo, em tempo constante."""
    if not segredo or not cabecalho:
        return False
    esperado = "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(cabecalho.strip().encode(), esperado.encode())

"""Adaptadores de pagamento (Pix/boleto) -- FASE7-1/2 da auditoria (04/09/2026).

Por adaptador, sem acoplar a um fornecedor específico -- decisão do
usuário: construir a interface agora, plugar um PSP real (Mercado Pago,
Asaas, Efí/Gerencianet, PagSeguro etc) quando houver credenciais de
sandbox. AdaptadorSandbox é a implementação de referência -- nunca fala
com um PSP de verdade, nunca dispara cobrança real (não realiza transações
reais em desenvolvimento/homologação, conforme instrução da Fase 7).

Achado FASE7-1/2 original: não existia integração real de nenhum PSP --
só um "gateway" simulado (EventoCobrancaSandbox) espalhado em dois
handlers de webhook divergentes (ver app/api/pagamentos.py para a
unificação). Este módulo é o contrato que qualquer adaptador real
implementaria; trocar de PSP no futuro significa escrever uma nova classe
aqui, sem tocar nos endpoints.
"""

import hashlib
import hmac
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

STATUS_WEBHOOK_VALIDOS: frozenset[str] = frozenset({"pago", "falhou", "estornado"})


@dataclass(frozen=True, slots=True)
class CobrancaCriada:
    """Resultado de criar uma cobrança Pix/boleto -- o que o operador
    mostra/envia ao cliente para ele pagar."""

    referencia_externa: str
    tipo: str
    qr_code: str | None = None
    linha_digitavel: str | None = None
    url_boleto: str | None = None
    expira_em: datetime | None = None


@dataclass(frozen=True, slots=True)
class EventoWebhookPagamento:
    """Evento normalizado de webhook -- todo adaptador traduz o payload
    específico do seu PSP para este formato comum antes de qualquer lógica
    de negócio (baixa de parcela, comissão etc) ser aplicada."""

    referencia: str
    organizacao_id: int
    parcela_id: int
    status: str
    valor: Decimal


class AdaptadorIndisponivelError(Exception):
    """O adaptador solicitado não existe ou não está configurado."""


class AssinaturaInvalidaError(Exception):
    """A assinatura do webhook não bateu -- payload rejeitado."""


class AdaptadorPagamento(ABC):
    nome: str

    @abstractmethod
    async def criar_cobranca(
        self, *, tipo: str, valor: Decimal, referencia: str, descricao: str, vencimento: date | None
    ) -> CobrancaCriada: ...

    @abstractmethod
    def verificar_assinatura(self, corpo: bytes, assinatura: str | None) -> bool: ...

    @abstractmethod
    def interpretar_webhook(self, payload: dict) -> EventoWebhookPagamento: ...


class AdaptadorSandbox(AdaptadorPagamento):
    """Implementação de referência -- nunca fala com um PSP real. QR code e
    linha digitável são valores de exemplo (não pagáveis de verdade), só
    para exercitar o fluxo de ponta a ponta -- criar cobrança, cliente
    "paga" no sandbox, webhook confirma -- sem depender de credenciais
    externas nem gerar cobrança real em nenhum ambiente."""

    nome = "sandbox"

    def __init__(self, segredo: str | None) -> None:
        self._segredo = segredo

    async def criar_cobranca(
        self, *, tipo: str, valor: Decimal, referencia: str, descricao: str, vencimento: date | None
    ) -> CobrancaCriada:
        if tipo == "pix":
            return CobrancaCriada(
                referencia_externa=referencia,
                tipo="pix",
                qr_code=f"00020126SANDBOX-NAO-PAGAVEL-{referencia}-{secrets.token_hex(4).upper()}",
            )
        if tipo == "boleto":
            return CobrancaCriada(
                referencia_externa=referencia,
                tipo="boleto",
                linha_digitavel=f"00190.00009 SANDBOX.{secrets.token_hex(6)}.NAOPAGAVEL 0",
            )
        raise ValueError(f"Tipo de cobrança não suportado: {tipo!r} (use \"pix\" ou \"boleto\")")

    def verificar_assinatura(self, corpo: bytes, assinatura: str | None) -> bool:
        if not self._segredo or not assinatura:
            return False
        esperado = hmac.new(self._segredo.encode(), corpo, hashlib.sha256).hexdigest()
        return hmac.compare_digest(assinatura, esperado)

    def interpretar_webhook(self, payload: dict) -> EventoWebhookPagamento:
        mapa_status = {"paid": "pago", "failed": "falhou", "refunded": "estornado"}
        status_bruto = payload.get("status")
        status = mapa_status.get(status_bruto, status_bruto)
        if status not in STATUS_WEBHOOK_VALIDOS:
            raise ValueError(f"Status de webhook desconhecido: {status_bruto!r}")
        try:
            return EventoWebhookPagamento(
                referencia=str(payload["referencia"]),
                organizacao_id=int(payload["organizacao_id"]),
                parcela_id=int(payload["parcela_id"]),
                status=status,
                valor=Decimal(str(payload["valor"])),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Payload de webhook incompleto: {exc}") from exc


def obter_adaptador(nome: str) -> AdaptadorPagamento:
    from app.settings import get_settings

    if nome == "sandbox":
        return AdaptadorSandbox(get_settings().gateway_webhook_secret)
    raise AdaptadorIndisponivelError(
        f'Adaptador de pagamento "{nome}" não está configurado. Só "sandbox" está '
        "disponível até um provedor real (Pix/boleto) ser integrado -- ver app/pagamentos.py."
    )

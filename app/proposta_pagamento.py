"""Condição de pagamento estruturada da proposta comercial.

Achado 18.5 da auditoria fina de Propostas (29/09/2026): a condição de
pagamento era só texto livre ("50% na contratação e 50% no protocolo" por
padrão), mas o financeiro gerava sempre parcela única vencendo no dia do
aceite -- o contas a receber mostrava 100% vencendo hoje, contradizendo o
que o cliente aceitou. Decisões do usuário (AskUserQuestion, 29/09/2026):

- a proposta passa a ter uma condição estruturada (à vista, entrada + protocolo
  ou parcelado), que o financeiro segue ao gerar os títulos do aceite;
- com condição estruturada, "pagamento confirmado" (que libera o SLA de
  protocolo e o jurídico) passa a exigir só a ENTRADA: a taxa GRU/INPI e a 1ª
  parcela dos honorários. À vista continua exigindo 100%;
- na "entrada + protocolo", a 2ª parcela nasce com vencimento provisório de
  30 dias e passa a vencer na data do protocolo quando ele é registrado.

Funções puras (sem sessão de banco), usadas por app/api/leads_propostas.py.
A condição fica em ``PropostaComercial.dados["condicao_pagamento"]`` -- sem
coluna nova; propostas antigas, sem a chave, continuam à vista (o
comportamento que o financeiro sempre teve).
"""

from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Literal

FormaPagamentoProposta = Literal["a_vista", "entrada_e_protocolo", "parcelado"]
FORMAS_PAGAMENTO_PROPOSTA: tuple[str, ...] = ("a_vista", "entrada_e_protocolo", "parcelado")
FORMAS_COM_ENTRADA = frozenset({"entrada_e_protocolo", "parcelado"})
PARCELAS_MINIMAS = 2
PARCELAS_MAXIMAS = 12
PRAZO_PROVISORIO_PROTOCOLO_DIAS = 30
INTERVALO_PARCELAS_DIAS = 30


def texto_condicao_pagamento(forma: str, parcelas: int | None = None) -> str:
    """Texto exibido na proposta (link público, PDF, portal) para a condição."""
    if forma == "entrada_e_protocolo":
        return (
            "Honorários: 50% no aceite da proposta e 50% no protocolo do pedido no INPI. "
            "Taxa GRU/INPI à vista, no aceite."
        )
    if forma == "parcelado":
        return (
            f"Honorários em {parcelas} parcelas mensais, a primeira no aceite da proposta. "
            "Taxa GRU/INPI à vista, no aceite."
        )
    return "À vista, no aceite da proposta."


def condicao_da_proposta(dados: dict | None) -> tuple[str, int | None]:
    """(forma, parcelas) gravadas na proposta; sem registro, à vista."""
    condicao = (dados or {}).get("condicao_pagamento") or {}
    forma = condicao.get("forma")
    if forma not in FORMAS_PAGAMENTO_PROPOSTA:
        return "a_vista", None
    parcelas = condicao.get("parcelas") if forma == "parcelado" else None
    return forma, parcelas


def dividir_em_parcelas(total: Decimal, quantidade: int) -> list[Decimal]:
    """Divide em centavos exatos; a diferença de arredondamento vai para a última."""
    centavos = Decimal("0.01")
    base = (Decimal(total) / quantidade).quantize(centavos, rounding=ROUND_DOWN)
    valores = [base] * quantidade
    valores[-1] += Decimal(total) - sum(valores)
    return valores


def cronograma_honorarios(
    valor: Decimal, forma: str, parcelas: int | None, hoje: date
) -> list[tuple[int, date, Decimal]]:
    """(número, vencimento, valor) das parcelas dos honorários no aceite."""
    if forma == "entrada_e_protocolo":
        entrada, saldo = dividir_em_parcelas(valor, 2)
        return [(1, hoje, entrada), (2, hoje + timedelta(days=PRAZO_PROVISORIO_PROTOCOLO_DIAS), saldo)]
    if forma == "parcelado" and parcelas and parcelas >= PARCELAS_MINIMAS:
        return [
            (indice + 1, hoje + timedelta(days=INTERVALO_PARCELAS_DIAS * indice), parcela)
            for indice, parcela in enumerate(dividir_em_parcelas(valor, parcelas))
        ]
    return [(1, hoje, Decimal(valor))]


def status_pagamento_por_parcelas(parcelas: list[tuple[str, int, str, str]]) -> str:
    """Status de pagamento da proposta com condição estruturada.

    Cada item é (componente, número da parcela, status da parcela, status do
    lançamento), onde componente é "honorarios" (ou "total", lançamento único
    legado) ou qualquer outro (ex.: "taxa-gru"). Confirmado quando a entrada
    está paga: a 1ª parcela dos honorários e todas as parcelas dos demais
    componentes. Lançamentos cancelados não contam, como no cálculo à vista.
    """
    ativas = [item for item in parcelas if item[3] != "cancelado"]
    if not ativas:
        return "cancelado" if parcelas else "pendente"
    exigidas = [
        item for item in ativas if not (item[0] in {"honorarios", "total"} and item[1] > 1)
    ]
    if exigidas and all(item[2] == "paga" for item in exigidas):
        return "confirmado"
    if any(item[2] == "paga" for item in ativas):
        return "parcial"
    return "pendente"

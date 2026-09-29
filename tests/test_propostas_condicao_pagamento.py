"""Achado 18.5 da auditoria fina de Propostas comerciais (29/09/2026).

A condição de pagamento era só texto livre ("50% na contratação e 50% no
protocolo" por padrão), mas o financeiro gerava sempre parcela única vencendo
no dia do aceite. Agora a proposta tem condição estruturada (à vista, entrada
+ protocolo ou parcelado), seguida pelo financeiro; com entrada, o pagamento
conta como confirmado (liberando SLA e jurídico) quando a entrada está paga.

Isolados e determinísticos: FakeSession (tests/conftest.py), sem banco real.
"""

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api.leads_propostas import (
    PropostaInput,
    _condicao_pagamento_proposta,
    _proposta_dict,
    calcular_pagamento_status_entrada_proposta,
    criar_contratacao_automatica_proposta,
)
from app.models import LancamentoFinanceiro, ParcelaFinanceira, PropostaComercial
from app.proposta_pagamento import (
    condicao_da_proposta,
    cronograma_honorarios,
    dividir_em_parcelas,
    somar_meses,
    status_pagamento_por_parcelas,
    texto_condicao_pagamento,
)
from tests.conftest import FakeResult, FakeSession

HOJE = date(2026, 9, 29)


# --- regras puras ----------------------------------------------------------------


def test_dividir_em_parcelas_fecha_os_centavos_na_ultima() -> None:
    assert dividir_em_parcelas(Decimal("1000.00"), 3) == [Decimal("333.33"), Decimal("333.33"), Decimal("333.34")]
    assert sum(dividir_em_parcelas(Decimal("1500.01"), 2)) == Decimal("1500.01")


def test_cronograma_entrada_e_protocolo_tem_segunda_parcela_provisoria_em_30_dias() -> None:
    assert cronograma_honorarios(Decimal("1500.00"), "entrada_e_protocolo", None, HOJE) == [
        (1, HOJE, Decimal("750.00")),
        (2, HOJE + timedelta(days=30), Decimal("750.00")),
    ]


def test_cronograma_parcelado_mensal_a_partir_do_aceite() -> None:
    cronograma = cronograma_honorarios(Decimal("1200.00"), "parcelado", 3, HOJE)
    assert [item[0] for item in cronograma] == [1, 2, 3]
    assert [item[1] for item in cronograma] == [HOJE, date(2026, 10, 29), date(2026, 11, 29)]
    assert sum(item[2] for item in cronograma) == Decimal("1200.00")


def test_cronograma_parcelado_nao_pula_mes_no_fim_do_mes() -> None:
    # Revisão do Codex no PR #149: com 30 dias fixos, o aceite em 31/01
    # vencia em 02/03 e 01/04, sem parcela em fevereiro.
    cronograma = cronograma_honorarios(Decimal("900.00"), "parcelado", 3, date(2026, 1, 31))
    assert [item[1] for item in cronograma] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]


def test_somar_meses_vira_o_ano() -> None:
    assert somar_meses(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_cronograma_a_vista_e_parcela_unica_no_aceite() -> None:
    assert cronograma_honorarios(Decimal("1500.00"), "a_vista", None, HOJE) == [(1, HOJE, Decimal("1500.00"))]


def test_proposta_sem_condicao_gravada_continua_a_vista() -> None:
    assert condicao_da_proposta({"conta_contabil_id": 1}) == ("a_vista", None)
    assert condicao_da_proposta(None) == ("a_vista", None)
    assert condicao_da_proposta({"condicao_pagamento": {"forma": "parcelado", "parcelas": 4}}) == ("parcelado", 4)


def test_texto_da_condicao_descreve_a_estrutura() -> None:
    assert "50% no aceite" in texto_condicao_pagamento("entrada_e_protocolo")
    assert "4 parcelas mensais" in texto_condicao_pagamento("parcelado", 4)
    assert texto_condicao_pagamento("a_vista") == "À vista, no aceite da proposta."


@pytest.mark.parametrize(
    ("parcelas", "esperado"),
    [
        # Entrada paga (1ª dos honorários + GRU): libera, mesmo com a 2ª em aberto.
        ([("honorarios", 1, "paga", "parcial"), ("honorarios", 2, "aberta", "parcial"), ("taxa-gru", 1, "paga", "pago")], "confirmado"),
        # GRU em aberto: a entrada não está completa.
        ([("honorarios", 1, "paga", "parcial"), ("honorarios", 2, "aberta", "parcial"), ("taxa-gru", 1, "aberta", "aberto")], "parcial"),
        ([("honorarios", 1, "aberta", "aberto"), ("taxa-gru", 1, "aberta", "aberto")], "pendente"),
        ([("honorarios", 1, "aberta", "cancelado")], "cancelado"),
        ([], "pendente"),
    ],
)
def test_status_por_parcelas_confirma_com_a_entrada(parcelas: list, esperado: str) -> None:
    assert status_pagamento_por_parcelas(parcelas) == esperado


# --- entrada da API ---------------------------------------------------------------


def test_parcelado_exige_numero_de_parcelas() -> None:
    with pytest.raises(ValidationError):
        PropostaInput(forma_pagamento="parcelado")


def test_parcelas_sao_ignoradas_fora_do_parcelado() -> None:
    assert PropostaInput(forma_pagamento="a_vista", parcelas=4).parcelas is None


def test_condicao_padrao_e_entrada_e_protocolo_com_texto_gerado() -> None:
    condicao, texto = _condicao_pagamento_proposta(PropostaInput())
    assert condicao == {"forma": "entrada_e_protocolo", "parcelas": None}
    assert texto == texto_condicao_pagamento("entrada_e_protocolo")


def test_cliente_antigo_com_texto_livre_mantem_texto_e_financeiro_a_vista() -> None:
    condicao, texto = _condicao_pagamento_proposta(PropostaInput(condicoes_pagamento="Boleto em 10 dias"))
    assert condicao == {"forma": "a_vista", "parcelas": None}
    assert texto == "Boleto em 10 dias"


def test_observacao_complementa_o_texto_gerado() -> None:
    condicao, texto = _condicao_pagamento_proposta(
        PropostaInput(forma_pagamento="parcelado", parcelas=3, condicoes_pagamento="Pix ou boleto")
    )
    assert condicao == {"forma": "parcelado", "parcelas": 3}
    assert texto.startswith(texto_condicao_pagamento("parcelado", 3))
    assert texto.endswith("Observações: Pix ou boleto")


def test_proposta_dict_expoe_a_condicao_estruturada() -> None:
    proposta = PropostaComercial(
        id=1, lead_id=9, numero="PROP-1", versao=1, escopo="Registro",
        dados={"condicao_pagamento": {"forma": "parcelado", "parcelas": 5}},
    )
    resultado = _proposta_dict(proposta)
    assert (resultado["forma_pagamento"], resultado["parcelas"]) == ("parcelado", 5)


# --- financeiro no aceite ---------------------------------------------------------


def _proposta_aceita(forma: str, parcelas: int | None = None) -> PropostaComercial:
    return PropostaComercial(
        id=10,
        organizacao_id=1,
        lead_id=9,
        numero="PROP-2026-000010",
        versao=1,
        escopo="Registro de marca no INPI",
        status="aceita",
        honorarios=Decimal("1500.00"),
        taxa_gru=Decimal("415.00"),
        dados={
            "conta_contabil_honorarios_id": 11,
            "conta_contabil_taxa_gru_id": 12,
            "condicao_pagamento": {"forma": forma, "parcelas": parcelas},
        },
    )


def _parcelas_por_lancamento(session: FakeSession) -> dict[str, list[ParcelaFinanceira]]:
    lancamentos = [obj for obj in session.adicionados if isinstance(obj, LancamentoFinanceiro)]
    parcelas = [obj for obj in session.adicionados if isinstance(obj, ParcelaFinanceira)]
    resultado: dict[str, list[ParcelaFinanceira]] = {}
    for lancamento in lancamentos:
        componente = lancamento.idempotency_key.rsplit(":", 1)[-1]
        # FakeSession atribui id=1 a todos no flush; separa pela ordem de criação.
        resultado[componente] = []
    ordem = list(resultado)
    indice = -1
    for parcela in parcelas:
        if parcela.numero == 1:
            indice += 1
        resultado[ordem[indice]].append(parcela)
    return resultado


def test_aceite_com_entrada_e_protocolo_gera_duas_parcelas_de_honorarios_e_gru_a_vista() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    asyncio.run(criar_contratacao_automatica_proposta(session, _proposta_aceita("entrada_e_protocolo"), "admin"))

    por_componente = _parcelas_por_lancamento(session)
    honorarios = por_componente["honorarios"]
    assert [(p.numero, p.valor) for p in honorarios] == [(1, Decimal("750.00")), (2, Decimal("750.00"))]
    assert honorarios[1].vencimento == honorarios[0].vencimento + timedelta(days=30)
    assert [(p.numero, p.valor) for p in por_componente["taxa-gru"]] == [(1, Decimal("415.00"))]


def test_aceite_parcelado_gera_as_parcelas_mensais_dos_honorarios() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    asyncio.run(criar_contratacao_automatica_proposta(session, _proposta_aceita("parcelado", 3), "admin"))

    honorarios = _parcelas_por_lancamento(session)["honorarios"]
    assert [p.numero for p in honorarios] == [1, 2, 3]
    assert sum(p.valor for p in honorarios) == Decimal("1500.00")


def test_aceite_de_proposta_antiga_sem_condicao_continua_parcela_unica() -> None:
    proposta = _proposta_aceita("a_vista")
    proposta.dados.pop("condicao_pagamento")
    session = FakeSession([FakeResult(scalar=None)])

    asyncio.run(criar_contratacao_automatica_proposta(session, proposta, "admin"))

    parcelas = [obj for obj in session.adicionados if isinstance(obj, ParcelaFinanceira)]
    assert [p.numero for p in parcelas] == [1, 1]


def test_status_da_entrada_le_as_parcelas_dos_lancamentos_da_proposta() -> None:
    session = FakeSession(
        [
            FakeResult(
                itens=[
                    ("proposta-aceite:10:honorarios", 1, "paga", "parcial"),
                    ("proposta-aceite:10:honorarios", 2, "aberta", "parcial"),
                    ("proposta-aceite:10:taxa-gru", 1, "paga", "pago"),
                ]
            )
        ]
    )

    assert asyncio.run(calcular_pagamento_status_entrada_proposta(session, 1, 10)) == "confirmado"

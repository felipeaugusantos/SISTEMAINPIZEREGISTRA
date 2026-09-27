import asyncio
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.api.contratacoes import ContratacaoInput, contratar_servico, listar_inadimplencia, obter_previsao_caixa
from app.models import ContratacaoServico, ParcelaFinanceira, PropostaComercial, ServicoFinanceiro
from tests.conftest import FakeResult, FakeSession, usuario_teste

# --- Fase 4 do plano proposta-financeiro (03/09/2026): contratação única por proposta ---


def test_contratar_servico_com_proposta_ja_contratada_devolve_idempotente() -> None:
    servico = ServicoFinanceiro(
        id=1, organizacao_id=1, codigo="REG-MARCA", nome="Registro de marca", valor=Decimal("1000"), ativo=True
    )
    proposta = PropostaComercial(
        id=7, organizacao_id=1, lead_id=9, numero="PROP-TEST", escopo="Registro de marca no INPI"
    )
    contratacao_existente = ContratacaoServico(id=5, organizacao_id=1, proposta_id=7, lancamento_id=9)

    session = FakeSession(
        [
            FakeResult(scalar=None),  # idempotency_key: nenhum lançamento com essa chave ainda
            FakeResult(scalar=1),  # conta contábil de receita da organização
            FakeResult(scalar=servico),
            FakeResult(scalar=proposta),
        ]
    )

    async def _commit_com_conflito() -> None:
        raise IntegrityError("insert", {}, Exception("uq_contratacao_servico_proposta"))

    session.commit = _commit_com_conflito
    session._resultados.append(FakeResult(scalar=contratacao_existente))

    resultado = asyncio.run(
        contratar_servico(
            ContratacaoInput(servico_id=1, proposta_id=7, conta_contabil_id=1, idempotency_key="chave-nova-diferente"),
            session,
            usuario_teste(),
        )
    )
    assert resultado == {"idempotente": True, "contratacao_id": 5, "lancamento_id": 9}


# --- Achado FASE7-11/12 da auditoria (04/09/2026): inadimplência agregada
# por faixa de atraso/cliente, e previsão de caixa (não existiam antes). ---


def _parcela(**kwargs: object) -> ParcelaFinanceira:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "lancamento_id": 1,
        "numero": 1,
        "valor": Decimal("1000"),
        "valor_pago": Decimal("0"),
        "status": "aberta",
    }
    base.update(kwargs)
    return ParcelaFinanceira(**base)


def test_listar_inadimplencia_agrega_por_faixa_e_cliente() -> None:
    hoje = date.today()
    parcela_recente = _parcela(id=1, vencimento=hoje - timedelta(days=10), valor=Decimal("500"))
    parcela_antiga = _parcela(id=2, vencimento=hoje - timedelta(days=100), valor=Decimal("2000"))
    session = FakeSession(
        [
            FakeResult(
                itens=[
                    (parcela_recente, 1, "Cliente A"),
                    (parcela_antiga, 2, "Cliente B"),
                ]
            )
        ]
    )

    resultado = asyncio.run(listar_inadimplencia(session, usuario_teste()))

    assert resultado["total"] == 2
    assert resultado["valor_total"] == "2500"
    assert resultado["por_faixa_atraso"]["1_30"] == "500"
    assert resultado["por_faixa_atraso"]["acima_90"] == "2000"
    # Cliente B (2000) deve vir antes de Cliente A (500) -- ordenado por total desc.
    assert [c["nome"] for c in resultado["por_cliente"]] == ["Cliente B", "Cliente A"]


def test_listar_inadimplencia_desconta_valor_ja_pago_parcialmente() -> None:
    hoje = date.today()
    parcela_parcial = _parcela(
        id=1, status="parcial", vencimento=hoje - timedelta(days=5), valor=Decimal("1000"), valor_pago=Decimal("300")
    )
    session = FakeSession([FakeResult(itens=[(parcela_parcial, None, None)])])

    resultado = asyncio.run(listar_inadimplencia(session, usuario_teste()))

    assert resultado["valor_total"] == "700"
    assert resultado["por_cliente"][0]["nome"] == "(sem cliente vinculado)"


def test_previsao_caixa_calcula_saldo_acumulado_por_semana() -> None:
    semana1 = date.today() + timedelta(days=1)
    semana2 = date.today() + timedelta(days=8)
    session = FakeSession(
        [
            FakeResult(
                itens=[
                    (semana1, "receber", Decimal("5000")),
                    (semana1, "pagar", Decimal("1000")),
                    (semana2, "receber", Decimal("2000")),
                    (semana2, "pagar", Decimal("3000")),
                ]
            )
        ]
    )

    resultado = asyncio.run(obter_previsao_caixa(session, usuario_teste(), 90))

    assert len(resultado["semanas"]) == 2
    assert resultado["semanas"][0]["saldo_semana"] == "4000"
    assert resultado["semanas"][0]["saldo_acumulado"] == "4000"
    # semana2: entradas 2000 - saidas 3000 = -1000; acumulado 4000 - 1000 = 3000
    assert resultado["semanas"][1]["saldo_semana"] == "-1000"
    assert resultado["semanas"][1]["saldo_acumulado"] == "3000"


def test_previsao_caixa_sem_movimentacao_devolve_lista_vazia() -> None:
    session = FakeSession([FakeResult(itens=[])])

    resultado = asyncio.run(obter_previsao_caixa(session, usuario_teste(), 30))

    assert resultado["semanas"] == []
    assert resultado["horizonte_dias"] == 30

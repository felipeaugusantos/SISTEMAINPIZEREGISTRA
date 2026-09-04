import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from starlette.requests import Request

from app.api.financeiro import BaixaCreate, Justificativa, baixar, cancelar, estornar
from app.models import FormaPagamentoFinanceira, LancamentoFinanceiro, ParcelaFinanceira, PropostaComercial
from tests.conftest import FakeResult, FakeSession, usuario_teste


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/financeiro/parcelas/1/baixar",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


# --- Fase 8 do plano proposta-financeiro (03/09/2026): financeiro sincroniza a proposta ---


def test_baixar_parcela_sincroniza_pagamento_da_proposta_vinculada() -> None:
    proposta = PropostaComercial(
        id=7, organizacao_id=1, lead_id=9, numero="PROP-TEST", escopo="x", pagamento_status="pendente"
    )
    lancamento = LancamentoFinanceiro(
        id=1,
        organizacao_id=1,
        proposta_id=7,
        tipo="receber",
        descricao="Honorários",
        competencia=date(2026, 1, 1),
        valor_total=Decimal("1855.00"),
        status="aberto",
        criado_por="sistema",
    )
    parcela = ParcelaFinanceira(
        id=1,
        organizacao_id=1,
        lancamento_id=1,
        numero=1,
        vencimento=date(2026, 1, 1),
        valor=Decimal("1855.00"),
        valor_pago=Decimal("0"),
        status="aberta",
    )
    lancamento.parcelas = [parcela]
    parcela.lancamento = lancamento
    forma = FormaPagamentoFinanceira(id=1, organizacao_id=1, nome="Pix", ativo=True)

    session = FakeSession(
        [
            FakeResult(scalar=parcela),  # _parcela
            FakeResult(scalar=forma),  # _forma_pagamento
            FakeResult(scalar=proposta),  # sincronizar_pagamento_proposta_por_id: busca a proposta
            FakeResult(itens=["pago"]),  # calcular_pagamento_status_proposta
            FakeResult(itens=[]),  # _documentacao_protocolavel
        ]
    )
    usuario = usuario_teste(perfil="financeiro", permissoes={"finance.manage"})
    resultado = asyncio.run(
        baixar(
            1,
            BaixaCreate(valor_pago=Decimal("1855.00"), pago_em=date(2026, 1, 5), forma_pagamento_id=1),
            _request(),
            session,
            usuario,
        )
    )
    assert resultado == {"status": "paga"}
    assert proposta.pagamento_status == "confirmado"


def test_estornar_parcela_sincroniza_pagamento_da_proposta_vinculada() -> None:
    proposta = PropostaComercial(
        id=7,
        organizacao_id=1,
        lead_id=9,
        numero="PROP-TEST",
        escopo="x",
        pagamento_status="confirmado",
        pagamento_confirmado_em=datetime.now(UTC),
    )
    lancamento = LancamentoFinanceiro(
        id=1,
        organizacao_id=1,
        proposta_id=7,
        tipo="receber",
        descricao="Honorários",
        competencia=date(2026, 1, 1),
        valor_total=Decimal("1855.00"),
        status="pago",
        criado_por="sistema",
    )
    parcela = ParcelaFinanceira(
        id=1,
        organizacao_id=1,
        lancamento_id=1,
        numero=1,
        vencimento=date(2026, 1, 1),
        valor=Decimal("1855.00"),
        valor_pago=Decimal("1855.00"),
        status="paga",
        forma_pagamento="Pix",
    )
    lancamento.parcelas = [parcela]
    parcela.lancamento = lancamento

    session = FakeSession(
        [
            FakeResult(scalar=parcela),  # _parcela
            # Achado FASE7-7 da auditoria (04/09/2026): estornar() agora
            # também cancela a comissão gerada na baixa (se houver) --
            # None aqui = nenhuma comissão foi gerada para essa parcela.
            FakeResult(scalar=None),  # _cancelar_comissao_da_parcela
            FakeResult(scalar=proposta),  # sincronizar_pagamento_proposta_por_id
            FakeResult(itens=["aberto"]),  # calcular_pagamento_status_proposta
            FakeResult(itens=[]),  # _documentacao_protocolavel
        ]
    )
    usuario = usuario_teste(perfil="financeiro", permissoes={"finance.approve"})
    resultado = asyncio.run(
        estornar(1, Justificativa(motivo="Pagamento revertido pelo cliente"), _request(), session, usuario)
    )
    assert resultado == {"status": "aberta"}
    assert proposta.pagamento_status == "pendente"


def test_cancelar_lancamento_sincroniza_pagamento_da_proposta_vinculada() -> None:
    proposta = PropostaComercial(
        id=7, organizacao_id=1, lead_id=9, numero="PROP-TEST", escopo="x", pagamento_status="pendente"
    )
    parcela = ParcelaFinanceira(
        id=1,
        organizacao_id=1,
        lancamento_id=1,
        numero=1,
        vencimento=date(2026, 1, 1),
        valor=Decimal("1855.00"),
        valor_pago=Decimal("0"),
        status="aberta",
    )
    lancamento = LancamentoFinanceiro(
        id=1,
        organizacao_id=1,
        proposta_id=7,
        tipo="receber",
        descricao="Honorários",
        competencia=date(2026, 1, 1),
        valor_total=Decimal("1855.00"),
        status="aberto",
        criado_por="sistema",
    )
    lancamento.parcelas = [parcela]

    session = FakeSession(
        [
            FakeResult(scalar=lancamento),  # select lancamento
            FakeResult(scalar=proposta),  # sincronizar_pagamento_proposta_por_id
            FakeResult(itens=["cancelado"]),  # calcular_pagamento_status_proposta
            FakeResult(itens=[]),  # _documentacao_protocolavel
        ]
    )
    usuario = usuario_teste(perfil="financeiro", permissoes={"finance.approve"})
    resultado = asyncio.run(
        cancelar(1, Justificativa(motivo="Proposta cancelada pelo cliente"), _request(), session, usuario)
    )
    assert resultado == {"status": "cancelado"}
    assert proposta.pagamento_status == "cancelado"

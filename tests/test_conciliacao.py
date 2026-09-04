from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

from app.api.conciliacao import (
    ConciliarManualInput,
    _tentar_conciliar_automaticamente,
    conciliar_manualmente,
    ignorar_transacao,
)
from app.models import ParcelaFinanceira, TransacaoBancaria
from tests.conftest import FakeResult, FakeSession, usuario_teste

# --- Achado FASE7-3 da auditoria (04/09/2026): conciliação bancária. ---


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/financeiro/conciliacao/1/conciliar",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
        }
    )


def _transacao(**kwargs: object) -> TransacaoBancaria:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "extrato_id": 1,
        "fitid": "abc123",
        "data": date(2026, 1, 15),
        "valor": Decimal("500.00"),
        "tipo": "credito",
        "descricao": "pagamento",
        "status": "pendente",
    }
    base.update(kwargs)
    return TransacaoBancaria(**base)


async def test_tentar_conciliar_automaticamente_com_uma_correspondencia() -> None:
    transacao = _transacao()
    session = FakeSession([FakeResult(itens=[]), FakeResult(itens=[42])])

    await _tentar_conciliar_automaticamente(session, 1, transacao)

    assert transacao.status == "conciliada"
    assert transacao.parcela_id == 42
    assert transacao.conciliado_por == "sistema (automático)"


async def test_tentar_conciliar_automaticamente_sem_correspondencia_fica_pendente() -> None:
    transacao = _transacao()
    session = FakeSession([FakeResult(itens=[]), FakeResult(itens=[])])

    await _tentar_conciliar_automaticamente(session, 1, transacao)

    assert transacao.status == "pendente"
    assert transacao.parcela_id is None


async def test_tentar_conciliar_automaticamente_com_multiplas_correspondencias_fica_pendente() -> None:
    transacao = _transacao()
    session = FakeSession([FakeResult(itens=[]), FakeResult(itens=[42, 43])])

    await _tentar_conciliar_automaticamente(session, 1, transacao)

    assert transacao.status == "pendente"


async def test_tentar_conciliar_automaticamente_exclui_parcelas_ja_vinculadas() -> None:
    transacao = _transacao()
    # duas candidatas por valor+data, mas uma já está vinculada a outra transação
    session = FakeSession([FakeResult(itens=[42]), FakeResult(itens=[42, 43])])

    await _tentar_conciliar_automaticamente(session, 1, transacao)

    assert transacao.status == "conciliada"
    assert transacao.parcela_id == 43


async def test_tentar_conciliar_automaticamente_ignora_debito() -> None:
    transacao = _transacao(tipo="debito")
    session = FakeSession()

    await _tentar_conciliar_automaticamente(session, 1, transacao)

    assert transacao.status == "pendente"
    assert session.executados == []


async def test_conciliar_manualmente_com_sucesso() -> None:
    transacao = _transacao()
    parcela = ParcelaFinanceira(id=42, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("500.00"))
    session = FakeSession([FakeResult(scalar=transacao), FakeResult(scalar=parcela)])

    resultado = await conciliar_manualmente(
        1, ConciliarManualInput(parcela_id=42), _request(), session, usuario_teste("administrador", {"finance.manage"})
    )

    assert resultado == {"status": "conciliada"}
    assert transacao.status == "conciliada"
    assert transacao.parcela_id == 42


async def test_conciliar_manualmente_ja_conciliada_retorna_409() -> None:
    transacao = _transacao(status="conciliada")
    session = FakeSession([FakeResult(scalar=transacao)])
    try:
        await conciliar_manualmente(
            1, ConciliarManualInput(parcela_id=42), _request(), session, usuario_teste("administrador", {"finance.manage"})
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409


async def test_conciliar_manualmente_valor_divergente_retorna_422() -> None:
    transacao = _transacao(valor=Decimal("500.00"))
    parcela = ParcelaFinanceira(id=42, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("999.00"))
    session = FakeSession([FakeResult(scalar=transacao), FakeResult(scalar=parcela)])
    try:
        await conciliar_manualmente(
            1, ConciliarManualInput(parcela_id=42), _request(), session, usuario_teste("administrador", {"finance.manage"})
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


async def test_ignorar_transacao_com_sucesso() -> None:
    transacao = _transacao()
    session = FakeSession([FakeResult(scalar=transacao)])

    resultado = await ignorar_transacao(1, _request(), session, usuario_teste("administrador", {"finance.manage"}))

    assert resultado == {"status": "ignorada"}
    assert transacao.status == "ignorada"


async def test_ignorar_transacao_ja_conciliada_retorna_409() -> None:
    transacao = _transacao(status="conciliada")
    session = FakeSession([FakeResult(scalar=transacao)])
    try:
        await ignorar_transacao(1, _request(), session, usuario_teste("administrador", {"finance.manage"}))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409

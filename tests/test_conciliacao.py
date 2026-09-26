import asyncio
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

from app.api.conciliacao import (
    TAMANHO_MAXIMO_OFX,
    ConciliarManualInput,
    _tentar_conciliar_automaticamente,
    conciliar_manualmente,
    ignorar_transacao,
    importar_extrato,
    listar_transacoes,
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
    session = FakeSession([FakeResult(itens=[42]), FakeResult(itens=[])])

    await _tentar_conciliar_automaticamente(session, 1, transacao)

    assert transacao.status == "conciliada"
    assert transacao.parcela_id == 42
    assert transacao.conciliado_por == "sistema (automático)"


async def test_tentar_conciliar_automaticamente_sem_correspondencia_fica_pendente() -> None:
    transacao = _transacao()
    session = FakeSession([FakeResult(itens=[])])

    await _tentar_conciliar_automaticamente(session, 1, transacao)

    assert transacao.status == "pendente"
    assert transacao.parcela_id is None


async def test_tentar_conciliar_automaticamente_com_multiplas_correspondencias_fica_pendente() -> None:
    transacao = _transacao()
    session = FakeSession([FakeResult(itens=[42, 43]), FakeResult(itens=[])])

    await _tentar_conciliar_automaticamente(session, 1, transacao)

    assert transacao.status == "pendente"


async def test_tentar_conciliar_automaticamente_exclui_parcelas_ja_vinculadas() -> None:
    transacao = _transacao()
    # duas candidatas por valor+data, mas uma já está vinculada a outra transação
    session = FakeSession([FakeResult(itens=[42, 43]), FakeResult(itens=[42])])

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
    parcela = ParcelaFinanceira(
        id=42, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("500.00"), status="paga"
    )
    session = FakeSession([FakeResult(scalar=transacao), FakeResult(scalar=parcela), FakeResult(scalar=None)])

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
    parcela = ParcelaFinanceira(
        id=42, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("999.00"), status="paga"
    )
    session = FakeSession([FakeResult(scalar=transacao), FakeResult(scalar=parcela)])
    try:
        await conciliar_manualmente(
            1, ConciliarManualInput(parcela_id=42), _request(), session, usuario_teste("administrador", {"finance.manage"})
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


def test_conciliar_manualmente_rejeita_transacao_debito() -> None:
    transacao = _transacao(tipo="debito")
    session = FakeSession([FakeResult(scalar=transacao)])
    try:
        asyncio.run(
            conciliar_manualmente(
                1, ConciliarManualInput(parcela_id=42), _request(), session,
                usuario_teste("administrador", {"finance.manage"}),
            )
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


def test_conciliar_manualmente_rejeita_parcela_nao_paga() -> None:
    transacao = _transacao()
    parcela = ParcelaFinanceira(
        id=42, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("500.00"), status="aberta"
    )
    session = FakeSession([FakeResult(scalar=transacao), FakeResult(scalar=parcela)])
    try:
        asyncio.run(
            conciliar_manualmente(
                1, ConciliarManualInput(parcela_id=42), _request(), session,
                usuario_teste("administrador", {"finance.manage"}),
            )
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


def test_conciliar_manualmente_rejeita_parcela_ja_vinculada() -> None:
    transacao = _transacao()
    parcela = ParcelaFinanceira(
        id=42, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("500.00"), status="paga"
    )
    session = FakeSession([FakeResult(scalar=transacao), FakeResult(scalar=parcela), FakeResult(scalar=99)])
    try:
        asyncio.run(
            conciliar_manualmente(
                1, ConciliarManualInput(parcela_id=42), _request(), session,
                usuario_teste("administrador", {"finance.manage"}),
            )
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409


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


async def test_listar_transacoes_retorna_total_real_e_paginacao() -> None:
    transacao = _transacao()
    session = FakeSession([FakeResult(scalar=205), FakeResult(itens=[transacao])])

    resultado = await listar_transacoes(
        session, usuario_teste("administrador", {"finance.view"}), None, limite=100, offset=200
    )

    assert resultado["total"] == 205
    assert resultado["offset"] == 200
    assert resultado["limite"] == 100
    assert resultado["proximo_offset"] is None
    assert len(resultado["itens"]) == 1


def test_importar_extrato_limita_leitura_antes_de_rejeitar_arquivo_grande() -> None:
    class ArquivoGrande:
        filename = "extrato.ofx"

        def __init__(self) -> None:
            self.bytes_lidos: int | None = None

        async def read(self, size: int = -1) -> bytes:
            self.bytes_lidos = size
            return b"x" * size

    arquivo = ArquivoGrande()
    try:
        asyncio.run(
            importar_extrato(
                _request(),
                FakeSession(),
                usuario_teste("administrador", {"finance.manage"}),
                arquivo,  # type: ignore[arg-type]
            )
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 413
    assert arquivo.bytes_lidos == TAMANHO_MAXIMO_OFX + 1

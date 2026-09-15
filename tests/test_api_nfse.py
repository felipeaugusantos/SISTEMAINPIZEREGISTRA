from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.api.nfse import EmitirNfseInput, cancelar_nfse, emitir_nfse
from app.models import EmpresaCRM, LancamentoFinanceiro, NotaFiscalServico
from tests.conftest import FakeResult, FakeSession, usuario_teste

# --- Achado FASE7-6 da auditoria (04/09/2026): emissão de NFS-e. ---


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/financeiro/nfse/emitir",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
        }
    )


def _lancamento(**kwargs: object) -> LancamentoFinanceiro:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "empresa_id": 9,
        "tipo": "receber",
        "descricao": "Registro de marca",
        "competencia": date(2026, 1, 1),
        "valor_total": Decimal("1500.00"),
        "status": "pago",
        "criado_por": "sistema",
    }
    base.update(kwargs)
    return LancamentoFinanceiro(**base)


def _empresa(**kwargs: object) -> EmpresaCRM:
    base: dict = {"id": 9, "organizacao_id": 1, "nome": "Cliente Teste", "documento": "12345678000199"}
    base.update(kwargs)
    return EmpresaCRM(**base)


async def test_emitir_nfse_lancamento_inexistente_retorna_404() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    try:
        await emitir_nfse(EmitirNfseInput(lancamento_id=999), _request(), session, usuario_teste("administrador", {"finance.manage"}))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 404


async def test_emitir_nfse_lancamento_a_pagar_retorna_422() -> None:
    lancamento = _lancamento(tipo="pagar")
    session = FakeSession([FakeResult(scalar=lancamento)])
    try:
        await emitir_nfse(EmitirNfseInput(lancamento_id=1), _request(), session, usuario_teste("administrador", {"finance.manage"}))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


async def test_emitir_nfse_lancamento_cancelado_retorna_422() -> None:
    lancamento = _lancamento(status="cancelado")
    session = FakeSession([FakeResult(scalar=lancamento)])
    try:
        await emitir_nfse(EmitirNfseInput(lancamento_id=1), _request(), session, usuario_teste("administrador", {"finance.manage"}))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


async def test_emitir_nfse_sem_documento_do_cliente_retorna_422() -> None:
    lancamento = _lancamento()
    empresa_sem_documento = _empresa(documento=None)
    session = FakeSession(
        [FakeResult(scalar=lancamento), FakeResult(scalar=None)], objetos_get=[empresa_sem_documento]
    )
    try:
        await emitir_nfse(EmitirNfseInput(lancamento_id=1), _request(), session, usuario_teste("administrador", {"finance.manage"}))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


async def test_emitir_nfse_com_sucesso() -> None:
    lancamento = _lancamento()
    empresa = _empresa()
    session = FakeSession([FakeResult(scalar=lancamento), FakeResult(scalar=None)], objetos_get=[empresa])

    resultado = await emitir_nfse(
        EmitirNfseInput(lancamento_id=1), _request(), session, usuario_teste("administrador", {"finance.manage"})
    )

    assert resultado["numero"].startswith("SANDBOX-")
    notas_criadas = [obj for obj in session.adicionados if isinstance(obj, NotaFiscalServico)]
    assert len(notas_criadas) == 1
    assert notas_criadas[0].status == "emitida"
    assert session.commits == 1


# --- Achado critico da auditoria financeira (15/09/2026): duplo clique ou
# retry apos timeout emitia duas NFS-e reais para o mesmo lancamento --
# nenhuma checagem pre-existente nem lock. ---


async def test_emitir_nfse_ja_emitida_para_lancamento_retorna_409() -> None:
    lancamento = _lancamento()
    session = FakeSession([FakeResult(scalar=lancamento), FakeResult(scalar=1)])

    try:
        await emitir_nfse(
            EmitirNfseInput(lancamento_id=1), _request(), session, usuario_teste("administrador", {"finance.manage"})
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409
    # Nunca chega a chamar o adaptador nem a persistir uma segunda nota.
    assert session.adicionados == []


async def test_emitir_nfse_violacao_do_indice_unico_no_flush_retorna_409() -> None:
    """Rede de seguranca do indice unico parcial (migration 562c64375107):
    mesmo se o pre-check e o lock falhassem por algum motivo, o flush() do
    INSERT ainda deveria bater na constraint do banco e devolver 409 em vez
    de vazar o IntegrityError."""
    lancamento = _lancamento()
    empresa = _empresa()
    session = FakeSession([FakeResult(scalar=lancamento), FakeResult(scalar=None)], objetos_get=[empresa])

    async def _flush_com_violacao_de_unicidade() -> None:
        raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))

    session.flush = _flush_com_violacao_de_unicidade

    try:
        await emitir_nfse(
            EmitirNfseInput(lancamento_id=1), _request(), session, usuario_teste("administrador", {"finance.manage"})
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409
    assert session.commits == 0


async def test_cancelar_nfse_inexistente_retorna_404() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    try:
        await cancelar_nfse(999, _request(), session, usuario_teste("administrador", {"finance.manage"}))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 404


async def test_cancelar_nfse_ja_cancelada_retorna_409() -> None:
    nota = NotaFiscalServico(
        id=1, organizacao_id=1, lancamento_id=1, adaptador="sandbox", numero="SANDBOX-ABC",
        valor=Decimal("1500"), status="cancelada", emitida_por="op", emitida_em=datetime.now(UTC),
    )
    session = FakeSession([FakeResult(scalar=nota)])
    try:
        await cancelar_nfse(1, _request(), session, usuario_teste("administrador", {"finance.manage"}))
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409


async def test_cancelar_nfse_com_sucesso() -> None:
    nota = NotaFiscalServico(
        id=1, organizacao_id=1, lancamento_id=1, adaptador="sandbox", numero="SANDBOX-ABC",
        valor=Decimal("1500"), status="emitida", emitida_por="op", emitida_em=datetime.now(UTC),
    )
    session = FakeSession([FakeResult(scalar=nota)])

    resultado = await cancelar_nfse(1, _request(), session, usuario_teste("administrador", {"finance.manage"}))

    assert resultado == {"status": "cancelada"}
    assert nota.status == "cancelada"

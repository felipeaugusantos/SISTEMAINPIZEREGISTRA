from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

import app.api.nfse as nfse_api
from app.api.nfse import EmitirNfseInput, cancelar_nfse, emitir_nfse, listar_lancamentos_disponiveis_nfse, listar_nfse
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


async def test_emitir_nfse_rejeita_componente_de_taxa_gru_da_proposta() -> None:
    lancamento = _lancamento(idempotency_key="proposta-aceite:12:taxa-gru")
    session = FakeSession([FakeResult(scalar=lancamento)])

    try:
        await emitir_nfse(
            EmitirNfseInput(lancamento_id=1),
            _request(),
            session,
            usuario_teste("administrador", {"finance.manage"}),
        )
        raise AssertionError("a taxa GRU/INPI não deve receber NFS-e neste fluxo")
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "GRU/INPI" in exc.detail


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


async def test_listar_nfse_apresenta_cliente_e_descricao_do_lancamento() -> None:
    nota = NotaFiscalServico(
        id=7, organizacao_id=1, lancamento_id=1, adaptador="sandbox", numero="SANDBOX-7",
        valor=Decimal("1500"), status="emitida", emitida_por="op", emitida_em=datetime.now(UTC),
    )
    lancamento = _lancamento(descricao="Honorários de registro")
    empresa = _empresa(nome="Marca Exemplo Ltda.")
    session = FakeSession([FakeResult(itens=[(nota, lancamento, empresa)])])

    resultado = await listar_nfse(session, usuario_teste("administrador", {"finance.view"}))

    assert resultado["itens"][0]["cliente"] == "Marca Exemplo Ltda."
    assert resultado["itens"][0]["descricao_lancamento"] == "Honorários de registro"
    assert resultado["itens"][0]["lancamento_id"] == 1


async def test_lancamentos_disponiveis_nfse_exige_empresa_da_organizacao() -> None:
    from fastapi import HTTPException

    session = FakeSession([FakeResult(scalar=None)])
    try:
        await listar_lancamentos_disponiveis_nfse(
            session, usuario_teste("administrador", {"finance.view"}), empresa_id=999
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 404


async def test_lancamentos_disponiveis_nfse_lista_titulos_do_cliente() -> None:
    session = FakeSession(
        [
            FakeResult(scalar=9),
            FakeResult(itens=[(1, "Honorários de registro", Decimal("1500"), date(2026, 1, 1))]),
        ]
    )

    resultado = await listar_lancamentos_disponiveis_nfse(
        session, usuario_teste("administrador", {"finance.view"}), empresa_id=9
    )

    assert resultado["itens"] == [
        {"id": 1, "descricao": "Honorários de registro", "valor": "1500", "competencia": date(2026, 1, 1)}
    ]


async def test_emitir_nfse_nao_expoe_ou_persiste_mensagem_bruta_do_provedor(monkeypatch) -> None:
    class AdaptadorComErro:
        async def emitir(self, **_kwargs):
            raise RuntimeError("token=secreto CPF=12345678901 payload interno")

    monkeypatch.setattr(nfse_api, "obter_adaptador_nfse", lambda _nome: AdaptadorComErro())
    session = FakeSession(
        [FakeResult(scalar=_lancamento()), FakeResult(scalar=None)],
        objetos_get=[_empresa()],
    )

    try:
        await emitir_nfse(
            EmitirNfseInput(lancamento_id=1), _request(), session, usuario_teste("administrador", {"finance.manage"})
        )
        raise AssertionError("deveria ter levantado HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "token=secreto" not in exc.detail
        assert "12345678901" not in exc.detail

    notas_erro = [obj for obj in session.adicionados if isinstance(obj, NotaFiscalServico)]
    assert len(notas_erro) == 1
    assert notas_erro[0].status == "erro"
    assert notas_erro[0].erro_detalhe == "Falha de comunicação com o provedor de NFS-e."
    assert "secreto" not in notas_erro[0].erro_detalhe
    assert "12345678901" not in notas_erro[0].erro_detalhe
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

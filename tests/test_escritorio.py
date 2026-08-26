from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.escritorio import CustoInput, criar_custo, exportar_custos
from app.auth import obter_usuario_atual
from app.main import app
from app.models import CustoJuridico
from tests.conftest import FakeResult, auth_override, usuario_teste


def test_api_escritorio_exige_permissao_financeira() -> None:
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste(perfil="operador", permissoes=set())
    )
    try:
        assert TestClient(app).get("/v1/admin/escritorio/departamentos").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_custo_idempotente_por_tenant() -> None:
    existente = CustoJuridico(
        id=7,
        organizacao_id=1,
        categoria="custas_inpi",
        descricao="GRU",
        valor=Decimal("100.00"),
        idempotency_key="gru-2026-001",
    )
    session = __import__("tests.conftest", fromlist=["FakeSession"]).FakeSession(
        [FakeResult(scalar=existente)]
    )
    dados = CustoInput(
        categoria="custas_inpi",
        descricao="GRU repetida",
        valor=Decimal("100.00"),
        idempotency_key="gru-2026-001",
    )
    import asyncio

    resposta = asyncio.run(
        criar_custo(dados, session, usuario_teste("administrador", {"finance.manage"}))
    )
    assert resposta["idempotente"] is True
    assert not session.adicionados


def test_webhook_sem_assinatura_e_rejeitado() -> None:
    resposta = TestClient(app).post(
        "/v1/webhooks/escritorio/financeiro",
        json={"organizacao_id": 1, "referencia": "evt-1", "evento": "paid", "payload": {}},
    )
    assert resposta.status_code == 401


def test_exportacao_nao_expoe_dados_de_fornecedor() -> None:
    from tests.conftest import FakeSession

    custo = CustoJuridico(
        id=9,
        organizacao_id=1,
        categoria="honorarios",
        descricao="Consultoria",
        valor=Decimal("250.00"),
        idempotency_key="honorarios-001",
        departamento_id=2,
    )
    resposta = __import__("asyncio").run(
        exportar_custos(
            FakeSession([FakeResult(itens=[custo])]),
            usuario_teste("administrador", {"finance.view"}),
        )
    )
    assert "250.00" in resposta.body.decode()
    assert "email" not in resposta.body.decode().lower()

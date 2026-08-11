from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.financeiro import STATUS_CLIENTE, _empresa_cliente, _mes_seguinte, _parcelar
from app.auth import obter_usuario_atual
from app.main import app
from app.models import StatusLead
from tests.conftest import auth_override, usuario_teste


def test_parcelamento_preserva_total_e_corre_datas() -> None:
    parcelas = _parcelar(Decimal("100.00"), 3)
    assert parcelas == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert sum(parcelas) == Decimal("100.00")
    assert _mes_seguinte(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert _mes_seguinte(date(2026, 1, 31), 2) == date(2026, 3, 31)


def test_pagina_financeira_e_protegida_por_permissao() -> None:
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste(perfil="operador", permissoes={"finance.view"})
    )
    try:
        with TestClient(app) as client:
            response = client.get("/admin/financeiro")
        assert response.status_code == 200
        assert "Controle contas a pagar e receber" in response.text
        assert "Novo lançamento" in response.text
    finally:
        app.dependency_overrides.pop(obter_usuario_atual, None)


def test_perfil_comercial_recebe_operacao_financeira_sem_aprovacao() -> None:
    from app.permissions import permissoes_do_perfil

    permissoes = permissoes_do_perfil("comercial")
    assert {"finance.view", "finance.manage", "finance.export"} <= permissoes
    assert "finance.approve" not in permissoes


def test_status_de_lead_que_formam_cliente_financeiro() -> None:
    assert STATUS_CLIENTE == {
        StatusLead.PROPOSTA_ENVIADA,
        StatusLead.CONVERTIDO,
    }
    assert StatusLead.EM_CONTATO not in STATUS_CLIENTE


def test_processo_monitorado_tambem_compõe_criterio_de_cliente() -> None:
    expressao = str(_empresa_cliente(usuario_teste()))
    assert "leads" in expressao
    assert "processos_monitorados" in expressao
    assert " OR " in expressao

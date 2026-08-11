from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import CanalContato, ContatoLead, Lead, StatusLead
from tests.conftest import FakeResult, auth_override, sessao_override, usuario_teste


def _registros() -> tuple[ContatoLead, Lead]:
    agora = datetime(2026, 8, 11, 15, 30, tzinfo=UTC)
    lead = Lead(
        id=22,
        organizacao_id=1,
        nome="Cliente CRM",
        email="cliente@empresa.com.br",
        telefone="11999998888",
        empresa="Empresa CRM",
        marca="ACME",
        origem="relatorio",
        status=StatusLead.EM_CONTATO,
    )
    lead.criado_em = agora
    lead.atualizado_em = agora
    contato = ContatoLead(
        id=10,
        organizacao_id=1,
        lead_id=lead.id,
        empresa_id=4,
        pesquisa_id="12345678-1234-1234-1234-123456789abc",
        operador_id=2,
        operador_nome="Operador CRM",
        canal=CanalContato.TELEFONE,
        resultado="Retorno agendado",
        observacao="Cliente solicitou nova ligação.",
    )
    contato.criado_em = agora
    return contato, lead


def test_historico_crm_lista_interacao_e_respeita_pii() -> None:
    contato, lead = _registros()
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=1),
        FakeResult(itens=[(CanalContato.TELEFONE, 1)]),
        FakeResult(itens=[(contato, lead, "ACME", "Empresa CRM")]),
    )
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste("operador", {"leads.view"})
    )
    try:
        resposta = TestClient(app).get("/v1/admin/crm/historico")
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["por_canal"]["telefone"] == 1
    assert corpo["itens"][0]["resultado"] == "Retorno agendado"
    assert corpo["itens"][0]["email"] == "c***@empresa.com.br"
    assert corpo["itens"][0]["telefone"] == "***8888"


def test_pagina_crm_exige_permissao_de_leads() -> None:
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste("operador", {"dashboard.view"})
    )
    try:
        resposta = TestClient(app).get("/admin/crm", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 403


def test_interface_crm_tem_menu_filtros_timeline_e_deeplink() -> None:
    pagina = Path("app/web/admin-crm.html").read_text(encoding="utf-8")
    script = Path("app/web/static/admin-crm.js").read_text(encoding="utf-8")
    shell = Path("app/web/static/admin-shell.js").read_text(encoding="utf-8")
    leads = Path("app/web/static/admin-leads.js").read_text(encoding="utf-8")
    assert 'data-admin-section="crm"' in pagina
    assert 'name="operador_id"' in pagina
    assert "/v1/admin/crm/historico" in script
    assert '"Atendimentos",data.por_canal.outro||0' in script
    assert 'label: "CRM"' in shell
    assert 'get("lead_id")' in leads
    assert "registrar_contato: true" in leads

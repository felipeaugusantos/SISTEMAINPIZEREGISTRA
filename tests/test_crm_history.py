from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import CanalContato, ContatoLead, Lead, LembreteCRM, StatusLead
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
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste("operador", {"crm.view"}))
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
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste("operador", {"dashboard.view"}))
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
    assert 'name="status_cliente"' in pagina
    assert 'id="new-reminder"' in pagina
    assert 'id="reminder-dialog"' in pagina
    assert "/v1/admin/crm/historico" in script
    assert "/v1/admin/crm/lembretes" in script
    assert "atualizar_cadastro" in script
    assert '["Atendimentos", data.por_canal.outro || 0]' in script
    assert 'label: "CRM"' in shell
    assert 'get("lead_id")' in leads
    assert "registrar_contato: true" in leads
    assert 'name="documento"' in leads
    assert "/admin/crm?lead_id=" in leads


def test_listar_lembretes_expoe_alertas_prazos_e_cadastros_antigos() -> None:
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
    lead.atualizado_em = datetime(2026, 1, 1, tzinfo=UTC)
    lembrete = LembreteCRM(
        id=7,
        organizacao_id=1,
        lead_id=lead.id,
        tipo="retorno",
        prioridade="alta",
        titulo="Retornar proposta",
        lembrar_em=datetime(2026, 8, 10, tzinfo=UTC),
        status="pendente",
        criado_por="operador@teste.local",
    )
    lembrete.lead = lead
    lembrete.responsavel = None
    lembrete.criado_em = agora
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(itens=[lembrete]),
        FakeResult(itens=[(1, 2, 3)]),
        FakeResult(scalar=1),
        FakeResult(itens=[(lead.id, lead.nome, lead.empresa, lead.atualizado_em)]),
    )
    usuario = usuario_teste("operador", {"crm.view", "crm.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get("/v1/admin/crm/lembretes")
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["metricas"] == {
        "vencidos": 1,
        "proximos_7_dias": 2,
        "pendentes": 3,
        "cadastros_para_atualizar": 1,
    }
    assert corpo["itens"][0]["titulo"] == "Retornar proposta"
    assert corpo["itens"][0]["vencido"] is True
    assert corpo["cadastros_para_atualizar"][0]["lead_id"] == lead.id


def test_criar_lembrete_vincula_cliente_e_audita() -> None:
    _, lead = _registros()
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead))
    usuario = usuario_teste("operador", {"crm.view", "crm.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/crm/lembretes",
            json={
                "lead_id": lead.id,
                "tipo": "atualizar_cadastro",
                "prioridade": "media",
                "titulo": "Confirmar CPF e telefone",
                "descricao": "Solicitar confirmação cadastral.",
                "lembrar_em": "2026-08-12T15:30:00Z",
            },
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 201
    assert resposta.json()["tipo"] == "atualizar_cadastro"
    assert resposta.json()["cliente"] == "Cliente CRM"

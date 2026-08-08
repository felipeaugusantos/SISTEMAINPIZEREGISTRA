from fastapi.testclient import TestClient

from app.auth import obter_usuario_atual
from app.main import app, web_dir
from tests.conftest import auth_override


def _client_autenticado() -> TestClient:
    app.dependency_overrides[obter_usuario_atual] = auth_override()
    return TestClient(app)


def _limpar_auth() -> None:
    app.dependency_overrides.pop(obter_usuario_atual, None)


def test_home_page() -> None:
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "Pesquisa de anterioridade de marcas" in response.text
    assert 'id="research-form"' in response.text
    assert "E-mail de contato" in response.text
    assert "Seção V — Marcas" in response.text
    assert "O que sua empresa oferece?" in response.text
    assert "Classe Nice" not in response.text
    assert "Tipo de pesquisa" not in response.text
    assert "Zé Registra" in response.text
    assert "/static/assets/personagem.png" in response.text


def test_process_detail_page() -> None:
    response = TestClient(app).get("/processos/935977333")

    assert response.status_code == 200
    assert 'id="process-detail"' in response.text
    assert "Movimentações na RPI" in response.text
    assert 'id="detail-contact-cta"' not in response.text


def test_report_page() -> None:
    response = TestClient(app).get("/relatorios/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 200
    assert 'id="report-content"' in response.text
    assert "Baixar resumo PDF" in response.text
    assert "Baixar resumo de 1 página" in response.text
    assert "Zé Registra" in response.text


def test_research_flow_requests_automatic_pdf_download() -> None:
    script = TestClient(app).get("/static/app.js")

    assert script.status_code == 200
    assert "?download=1" in script.text


def test_admin_leads_requires_authentication() -> None:
    client = TestClient(app)
    assert client.get("/admin/leads", follow_redirects=False).status_code == 303
    client = _client_autenticado()
    response = client.get("/admin/leads")

    assert response.status_code == 200
    assert "Leads e pesquisas" in response.text
    assert 'data-admin-section="leads"' in response.text
    assert "/static/admin-leads.css?v=3" in response.text
    assert client.get("/static/admin-leads.css").status_code == 200
    _limpar_auth()


def test_unified_admin_dashboard_requires_authentication() -> None:
    client = TestClient(app)
    assert client.get("/admin", follow_redirects=False).status_code == 303
    client = _client_autenticado()
    response = client.get("/admin")

    assert response.status_code == 200
    assert "Centro de operações" in response.text
    assert "Pendências que pedem atenção" in response.text
    assert 'data-admin-section="overview"' in response.text
    assert 'id="rpi-monitor"' in response.text
    assert 'id="rpi-sync-now"' in response.text
    assert 'id="rpi-history"' in response.text

    script = client.get("/static/admin-dashboard.js")
    assert script.status_code == 200
    assert 'fetch("/v1/admin/rpi")' in script.text
    assert 'fetch(url, { method: "POST" })' in script.text
    _limpar_auth()


def test_monitoramento_rpi_exige_autenticacao() -> None:
    assert TestClient(app).get("/v1/admin/rpi").status_code == 401


def test_semantic_admin_routes_keep_modules_available() -> None:
    client = _client_autenticado()
    assert client.get("/admin/pesquisas").status_code == 200
    assert client.get("/admin/validacao").status_code == 200
    assert client.get("/admin/risco").status_code == 200
    assert client.get("/admin/ia").status_code == 200
    learning = client.get("/admin/aprendizado")
    assert learning.status_code == 200
    assert "Aprendizado supervisionado" in learning.text
    production = client.get("/admin/producao")
    assert production.status_code == 200
    assert "Produção e auditoria" in production.text
    assert production.headers["cache-control"] == "no-store"
    assert production.headers["x-frame-options"] == "DENY"
    assert client.get("/admin/usuarios").status_code == 200
    _limpar_auth()


def test_learning_panel_has_responsive_control_grid() -> None:
    learning = (web_dir / "admin-aprendizado.html").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'class="learning-rollout-control"' in learning
    assert 'id="learning-min-test-samples"' in learning
    assert "A avaliação humana e as metas abaixo aprimoram o modelo, mas não bloqueiam" in learning
    assert "Exibição preliminar automática ao cliente" in learning
    assert ".production-grid.learning-control-grid" in styles
    assert ".learning-control-grid .learning-thresholds input" in styles
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in styles
    assert ".learning-review-fields input" in styles
    assert ".learning-review-fields select" in styles
    assert ".learning-review-fields textarea" in styles
    assert ".label-review .learning-actions" in styles
    assert ".prediction-review > .primary-button" in styles


def test_leads_exibe_status_e_acao_do_relatorio_completo() -> None:
    page = (web_dir / "admin-leads.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "admin-leads.css").read_text(encoding="utf-8")

    assert "admin-leads.js?v=20" in page
    assert "Completo não gerado" in script
    assert "Completo gerado" in script
    assert "/relatorio-completo.pdf" in script
    assert 'method: "POST"' in script
    assert ".full-report-status.ready" in styles
    assert ".full-report-status.pending" in styles


def test_menu_de_usuarios_existe_em_todas_as_paginas_admin() -> None:
    script = TestClient(app).get("/static/admin-shell.js")
    assert script.status_code == 200
    assert 'label: "Usuários e acessos"' in script.text
    assert 'data-permission="${section.permission}"' in script.text
    for arquivo in (
        "admin.html",
        "admin-leads.html",
        "admin-fase2.html",
        "admin-fase3.html",
        "admin-fase4.html",
        "admin-producao.html",
        "admin-aprendizado.html",
        "admin-usuarios.html",
    ):
        conteudo = (web_dir / arquivo).read_text(encoding="utf-8")
        assert "admin-shell.js?v=19" in conteudo
        assert "styles.css?v=" in conteudo


def test_central_de_analise_unifica_etapas_e_ajuda_contextual() -> None:
    client = TestClient(app)
    assert client.get("/admin/analises/pesquisa-1", follow_redirects=False).status_code == 303
    client = _client_autenticado()
    response = client.get("/admin/analises/pesquisa-1")
    assert response.status_code == 200
    assert "Central de análise" in response.text
    assert 'id="analysis-help-open"' in response.text
    assert "Como trabalhar nesta tela" in response.text
    script = client.get("/static/admin-analise.js")
    assert script.status_code == 200
    for etapa in (
        "Validação técnica",
        "Motor determinístico de risco",
        "Aprendizado supervisionado",
        "IA explicativa",
        "Parecer humano",
        "Relatório completo",
    ):
        assert etapa in script.text
    shell = client.get("/static/admin-shell.js").text
    assert "Leads, pesquisas e análises" in shell
    assert 'href: "/admin/validacao"' not in shell
    _limpar_auth()


def test_admin_phase2_requires_authentication() -> None:
    client = TestClient(app)
    assert client.get("/admin/fase2", follow_redirects=False).status_code == 303
    response = _client_autenticado().get("/admin/fase2")

    assert response.status_code == 200
    assert "Validação técnica" in response.text
    _limpar_auth()


def test_admin_phase3_requires_authentication() -> None:
    client = TestClient(app)
    assert client.get("/admin/fase3", follow_redirects=False).status_code == 303
    response = _client_autenticado().get("/admin/fase3")

    assert response.status_code == 200
    assert "Motor determinístico de risco" in response.text
    _limpar_auth()


def test_admin_phase4_requires_authentication() -> None:
    client = TestClient(app)
    assert client.get("/admin/fase4", follow_redirects=False).status_code == 303
    response = _client_autenticado().get("/admin/fase4")

    assert response.status_code == 200
    assert "IA explicativa" in response.text
    assert "não são enviados ao modelo" in response.text
    _limpar_auth()


def test_privacy_page() -> None:
    response = TestClient(app).get("/privacidade")

    assert response.status_code == 200
    assert "Aviso de privacidade" in response.text


def test_institutional_pages() -> None:
    client = TestClient(app)
    sobre = client.get("/sobre")
    contato = client.get("/contato")

    assert sobre.status_code == 200
    assert "Clareza antes de dar o próximo passo" in sobre.text
    assert contato.status_code == 200
    assert "contato@zeregistra.com.br" in contato.text

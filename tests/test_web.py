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
    assert "/static/admin-leads.css?v=12" in response.text
    assert client.get("/static/admin-leads.css").status_code == 200
    script = client.get("/static/admin-leads.js")
    assert "renderPropostas" in script.text
    assert "/v1/admin/leads/${lead.id}/propostas" in script.text
    _limpar_auth()


def test_observabilidade_restrita_a_perfil_tech() -> None:
    client = TestClient(app)
    assert client.get("/admin/observabilidade", follow_redirects=False).status_code == 303
    client = _client_autenticado()
    response = client.get("/admin/observabilidade")
    assert response.status_code == 200
    assert "Observabilidade" in response.text
    assert 'data-admin-section="observability"' in response.text
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
    assert 'id="rpi-recent-executions" hidden' in response.text
    assert 'id="overview-priorities"' in response.text
    assert 'class="overview-section overview-module-section"' in response.text
    assert response.text.index('class="overview-section overview-module-section"') < response.text.index(
        'id="overview-message"'
    )

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
    assert client.get("/admin/ia").status_code == 404
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
    assert "gates técnicos, revisão humana mínima" in learning
    assert "Modelos SHADOW e DISABLED nunca aparecem ao cliente" in learning
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

    assert "admin-leads.js?v=" in page
    assert "Pipeline de atendimento" in page
    assert 'data-priority="atrasadas"' in page
    assert "Histórico de pesquisas" in page
    assert "Última pesquisa" in page
    assert "Por contato" in page
    assert "Por pesquisa" in page
    assert "Revisão obrigatória" in script
    assert 'item.analysis_state !== "VALIDATED"' in script
    assert "Completo gerado" in script
    assert "/relatorio-completo.pdf" in script
    assert 'method: "POST"' in script
    assert ".full-report-status.ready" in styles
    assert ".full-report-status.pending" in styles
    assert "relatorios_completos_gerados" in script
    assert "Maior risco:" in script
    assert "Ver pesquisas" in script
    assert 'fetch("/v1/admin/leads-crm")' in script
    assert 'params.set("prioridade", state.priority)' in script
    assert 'class="danger-button request-delete-research"' in script
    assert ".lead-row-actions .request-delete-research" in styles
    assert "background: #b63f2d" in styles
    assert 'form.elements.proxima_acao_em.value = ""' in script
    assert 'form.elements.tags.value = ""' in script
    assert 'form.elements.notas.value = ""' in script
    assert "Formulário pronto para um novo registro" in script
    assert 'data-label="Observações"' in script
    assert '<section class="lead-history lg-full">' in script
    assert "table.lead-docs td::before" in styles
    assert ".chk-actions > .chk-padrao" in styles


def test_painel_do_lead_usa_abas() -> None:
    # Achado 05/09/2026: pedido do usuario para trocar a lista longa e
    # empilhada por abas -- Atendimento Comercial, Empresa, Funil do Lead,
    # Linha do tempo, Documentos do atendimento, Guias do INPI e Proposta de
    # registro, com o card de identificacao e o Portal do cliente fixos no
    # topo (fora das abas).
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "admin-leads.css").read_text(encoding="utf-8")

    for aba in [
        "Atendimento Comercial",
        "Empresa",
        "Funil do Lead",
        "Linha do tempo",
        "Documentos do atendimento",
        "Guias do INPI",
        "Proposta de registro",
    ]:
        assert aba in script

    assert 'class="lead-tabs"' in script
    assert 'data-panel="atendimento"' in script
    assert 'data-panel="empresa"' in script
    assert 'data-panel="funil"' in script
    assert 'data-panel="timeline"' in script
    assert 'data-panel="documentos"' in script
    assert 'data-panel="guias"' in script
    assert 'data-panel="propostas"' in script
    assert script.index('class="lead-contact-summary"') < script.index('class="lead-tabs"')
    # Checklist entra na aba do funil, Contatos realizados na aba de atendimento.
    assert script.index('data-panel="atendimento"') < script.index('class="lead-contact-log"')
    assert script.index('data-panel="funil"') < script.index('id="lead-checklist"')
    assert ".lead-tabs" in styles
    assert ".lead-tab.active" in styles
    assert ".lead-tab-panel" in styles


def test_portal_cliente_confirma_envio_de_documento_e_lista_arquivos() -> None:
    # Achado da validação do Portal do Cliente (17/09/2026): o formulário de
    # envio de documento não tinha tratamento de erro (upload falho passava
    # em silêncio) nem mostrava confirmação/lista dos arquivos já enviados
    # -- o endpoint GET /v1/portal/arquivos existia no backend, mas nunca
    # era consultado pelo frontend.
    page = (web_dir / "portal-cliente.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "portal-cliente.js").read_text(encoding="utf-8")

    assert 'id="sent-files"' in page
    assert 'id="file-status"' in page
    assert 'id="message-status"' in page
    assert 'api("/v1/portal/arquivos")' in script
    assert "async function carregarArquivosEnviados" in script
    # O handler de envio agora trata erro (try/catch) em vez de deixar a
    # falha passar em silêncio.
    assert script.count('status.className = "status-message error"') >= 2


def test_admin_visualiza_arquivos_enviados_pelo_cliente_no_portal() -> None:
    # Achado da validação do Portal do Cliente (17/09/2026): documentos
    # enviados pelo cliente (ArquivoClientePortal) não tinham NENHUMA tela
    # administrativa equivalente -- a equipe não conseguia ver nem baixar o
    # que o cliente enviava pelo portal.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert "async function renderPortalArquivos" in script
    assert 'fetch(`/v1/admin/leads/${lead.id}/portal-arquivos`)' in script
    assert "renderPortalArquivos(lead);" in script


def test_registrar_atendimento_nao_quebra_para_lead_sem_pesquisa() -> None:
    # Achado da auditoria de Leads/CRM (10/09/2026, Hipótese 8): o diálogo
    # dedicado "Registrar atendimento" exigia um <select required> só com
    # as pesquisas do lead -- para um lead sem nenhuma, ficava vazio e o
    # navegador bloqueava o envio silenciosamente, sem explicar nada.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    # abrirRegistroAtendimento cai para o diálogo completo (que já funciona
    # sem pesquisa, via "Salvar atendimento") em vez de montar o formulário
    # com o select vazio.
    assert "if (!(lead.pesquisas || []).length) {" in script
    assert script.index("abrirRegistroAtendimento") < script.index("if (!(lead.pesquisas || []).length) {")
    assert "await openLead(lead.id);" in script

    # A seção "Contatos realizados" do diálogo completo explica o caminho
    # alternativo em vez de desaparecer sem nenhum aviso.
    assert 'Este lead ainda não tem pesquisa de marca vinculada' in script
    assert 'Use "Salvar atendimento" acima' in script


def test_tela_de_leads_permite_distribuir_sem_responsavel_em_lote() -> None:
    # Achado de produto/UX (item 3, 16/09/2026): o endpoint de distribuição
    # em lote (POST /v1/admin/leads/distribuir) já existia e já era usado
    # na tela de CRM/Kanban (admin-crm.js), mas não tinha nenhuma ação
    # equivalente na tela de Leads -- quem via o card "Sem responsável"
    # acumular tinha que trocar de tela pra resolver.
    page = (web_dir / "admin-leads.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert 'id="distribute-leads"' in page
    assert 'fetch("/v1/admin/leads/distribuir"' in script
    assert "atualizarBotaoDistribuir" in script
    # Só aparece para quem gerencia leads e só quando há algo pra distribuir.
    assert "botao.hidden = !state.canManage || state.semResponsavel === 0;" in script


def test_cards_do_radar_prospeccao_nao_encolhem_alem_do_conteudo() -> None:
    # Achado do usuário (17/09/2026): minmax(0,1fr) deixava as 6 colunas do
    # funil encolherem sem limite -- em telas menos largas o número do card
    # quebrava no meio (ex.: "495" virava "49"/"5" em duas linhas). Mesmo
    # padrão de .lead-metrics (styles.css): largura mínima + rolagem
    # horizontal em vez de espremer o conteúdo.
    page = (web_dir / "admin-prospeccao.html").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "admin-prospeccao.css").read_text(encoding="utf-8")

    assert "admin-prospeccao.css?v=" in page
    assert ".prospeccao-metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}" not in styles
    assert ".prospeccao-metrics{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:12px;overflow-x:auto}" in styles
    assert ".prospeccao-metric strong{display:block;font-size:1.5rem;color:#076b4c;white-space:nowrap}" in styles


def test_tela_de_script_atendimento_existe_com_crud_completo() -> None:
    # Item 1 da lista de melhorias de produto (15/09/2026): modelo de 1º
    # atendimento + opção de adicionar mais modelos manualmente. Reaproveita
    # o padrão de Modelo de e-mail (leads)/Modelo de propostas (texto em
    # Organizacao.branding), mas como lista com CRUD (criar/editar/excluir).
    page = (web_dir / "admin-script-atendimento.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-script-atendimento.js").read_text(encoding="utf-8")
    shell = (web_dir / "static" / "admin-shell.js").read_text(encoding="utf-8")

    assert "admin-script-atendimento.js?v=" in page
    assert 'id="new-script"' in page
    assert 'data-token="{{lead.nome}}"' in page
    assert 'api("/v1/admin/configuracao/scripts-atendimento")' in script
    assert 'method: id ? "PUT" : "POST"' in script
    assert "/v1/admin/configuracao/scripts-atendimento/${del.dataset.id}" in script
    assert 'method: "DELETE"' in script
    assert "/admin/configuracao/script-atendimento" in shell


def test_dossie_envia_e_exibe_parecer_unico() -> None:
    page = (web_dir / "admin-analise.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-analise.js").read_text(encoding="utf-8")

    assert "admin-analise.js?v=" in page
    assert "admin-analise.css?v=" in page
    assert 'observacoes_humanas: values.get("observacoes_humanas")' in script
    assert "consolidated-review-form" in script
    assert "executar-agente" not in script
    assert "learning-review-form" not in script
    assert "risk-review-form" not in script
    assert "Salvar parecer único" in script


def test_dossie_exibe_legenda_da_pontuacao_de_risco() -> None:
    script = (web_dir / "static" / "admin-analise.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "admin-analise.css").read_text(encoding="utf-8")

    assert 'range: "0–24"' in script
    assert 'range: "25–49"' in script
    assert 'range: "50–74"' in script
    assert 'range: "75–100"' in script
    assert "esta pontuação mede risco de conflito" in script
    assert "Ela não é um percentual de chance" in script
    assert "riskLegend(item)" in script
    assert ".risk-range.active" in styles


def test_dossie_exibe_matriz_oficial_de_registrabilidade() -> None:
    page = (web_dir / "admin-analise.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-analise.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "admin-analise.css").read_text(encoding="utf-8")

    assert "Análise de registrabilidade" in page
    assert "Matriz de Registrabilidade INPI" in script
    assert "Não analisado" in script
    assert "Consultar Manual de Marcas do INPI" in script
    assert "officialMatrix(item.matriz, data.permissoes.validacao_revisar)" in script
    assert ".official-rule.possivel_impedimento" in styles
    assert "Completar análise oficial" in script
    assert 'id="registrability-form"' in page
    assert "Salvar e recalcular matriz" in page


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
        "admin-producao.html",
        "admin-aprendizado.html",
        "admin-usuarios.html",
    ):
        conteudo = (web_dir / arquivo).read_text(encoding="utf-8")
        assert "admin-shell.js?v=" in conteudo
        assert "styles.css?v=" in conteudo


def test_producao_expoe_paginacao_da_auditoria() -> None:
    html = (web_dir / "admin-producao.html").read_text(encoding="utf-8")
    javascript = (web_dir / "static" / "admin-producao.js").read_text(encoding="utf-8")

    assert 'id="audit-prev"' in html
    assert 'id="audit-next"' in html
    assert 'id="audit-page-summary"' in html
    assert ">Próxima</button>" in html
    assert "PrÃ³xima" not in html
    assert 'class="lead-pagination"' in html
    assert "styles.css?v=" in html
    assert "admin-producao.js?v=13" in html
    assert "pageSize: 10" in javascript
    assert "limite_auditoria" in javascript
    assert "deslocamento_auditoria" in javascript


def test_consulta_de_marcas_no_menu_e_pagina_servida() -> None:
    script = TestClient(app).get("/static/admin-shell.js")
    assert 'label: "Consulta de marcas"' in script.text
    assert 'href: "/admin/consulta"' in script.text
    html = (web_dir / "admin-consulta.html").read_text(encoding="utf-8")
    assert 'name="marca"' in html
    assert 'name="atividade"' in html
    assert "admin-shell.js?v=" in html


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
        "Análise de registrabilidade",
        "Parecer humano",
        "Workflow humano e relatório",
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


def test_rotas_da_ia_explicativa_foram_removidas() -> None:
    client = TestClient(app)
    assert client.get("/admin/fase4").status_code == 404
    assert client.get("/admin/ia").status_code == 404
    assert client.get("/v1/admin/fase4").status_code == 404


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


def test_confiabilidade_expoe_reprocessamento_dos_agentes() -> None:
    pagina = (web_dir / "admin-confiabilidade.html").read_text(encoding="utf-8")

    assert "admin-confiabilidade.js?v=9" in pagina
    assert 'data-job="registrabilidade.reprocessar_agentes"' in pagina
    assert "Reprocessar agentes pendentes" in pagina



def test_politica_crm_usa_permissao_correta_e_checkboxes_compactos() -> None:
    page = (web_dir / "admin-regras-automaticas.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-regras-automaticas.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "admin-crm-overrides.css").read_text(encoding="utf-8")

    assert "admin-crm-overrides.css?v=6" in page
    assert "admin-regras-automaticas.js?v=5" in page
    assert 'includes("crm.manage")' in script
    assert 'includes("leads.manage")' not in script
    assert "politicaErrorDetail" in script
    assert 'type="checkbox"' in styles
    assert "height: 18px" in styles
    assert "width: 18px" in styles

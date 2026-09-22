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
    # Achado do usuário (21/09/2026): além do sino, uma notificação nativa
    # do navegador/sistema avisa quando chega mensagem nova do cliente --
    # reaproveita o polling de 60s já existente pro sino.
    assert "function notificarMensagensNovas" in script.text
    assert "Notification.requestPermission" in script.text
    assert 'id="notif-native-toggle"' in response.text
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


def test_aba_ativa_do_lead_tem_contraste_legivel() -> None:
    # Achado do usuário (20/09/2026): a aba ativa (ex.: "Linha do tempo")
    # mostrava texto branco sobre fundo praticamente branco, ilegível --
    # causa raiz: `var(--green)` nunca foi definida em nenhum arquivo CSS
    # (o token real é `--forest`), então o `background` da regra falhava
    # silenciosamente e caía no fundo claro do container pai. O mesmo
    # `var(--green)` inexistente também quebrava (sem erro visível) o
    # destaque de cor em Financeiro, CRM e RPI -- corrigido em todos os
    # arquivos de uma vez, por ser o mesmo bug mecânico.
    for nome in (
        "admin-leads.css",
        "admin-crm.css",
        "admin-crm-overrides.css",
        "admin-crm-reminders.css",
        "admin-financeiro.css",
        "admin-financeiro-log.css",
        "styles.css",
    ):
        conteudo = (web_dir / "static" / nome).read_text(encoding="utf-8")
        assert "var(--green)" not in conteudo, f"var(--green) não existe -- {nome} ainda referencia"

    styles = (web_dir / "static" / "admin-leads.css").read_text(encoding="utf-8")
    assert ".lead-tab.active {\n  color: #fff;\n  background: var(--forest);\n}" in styles


def test_resumo_de_contato_do_lead_nao_esprime_colunas_no_desktop() -> None:
    # Achado do usuário (20/09/2026): com `repeat(5, minmax(0, 1fr))` fixo,
    # qualquer largura de desktop entre 980px e o max-width do diálogo
    # (920px) espremia cada coluna do resumo de contato a ~159px -- estreito
    # demais pro botão "WhatsApp" (quebrava em duas linhas, "WhatsA"/"pp")
    # e pro rótulo "Prioridade (IA)" (cortava com reticências).
    styles = (web_dir / "static" / "admin-leads.css").read_text(encoding="utf-8")
    assert "repeat(5, minmax(0, 1fr))" not in styles
    assert "grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));" in styles


def test_icones_do_resumo_de_contato_usam_data_icon_nao_posicao() -> None:
    # Achado do usuário (20/09/2026): o mapeamento de ícones por nth-child
    # ficou desatualizado quando o campo "Site" (oculto) foi inserido entre
    # Telefone e CPF/CNPJ -- CPF/CNPJ passou a herdar o ícone errado ("OK")
    # e Empresa ficou sem ícone nenhum. Atributos data-icon são imunes a
    # mudanças de posição/visibilidade dos campos.
    styles = (web_dir / "static" / "admin-leads.css").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert "nth-child(1)::before" not in styles
    assert 'div[data-icon="email"]::before { content: "@"' in styles
    assert 'div[data-icon="telefone"]::before { content: "TEL"' in styles
    assert 'div[data-icon="documento"]::before { content: "PJ"' in styles
    assert 'div[data-icon="empresa"]::before { content: "OK"' in styles

    assert '<div data-icon="email"><span>E-mail</span>' in script
    assert '<div data-icon="telefone"><span>Telefone</span>' in script
    assert '<div data-icon="documento"><span>CPF/CNPJ</span>' in script
    assert '<div data-icon="empresa"><span>Empresa</span>' in script


def test_ficha_do_lead_tem_atalhos_de_busca_para_a_empresa() -> None:
    # Pedido do usuário (20/09/2026): ajudar o atendimento a achar site e
    # Instagram do cliente. Sem API paga de enriquecimento contratada, a
    # saída sem custo é um atalho de busca pronto (Google e Google
    # restrito a instagram.com) com o nome da empresa já preenchido --
    # poupa o atendente de abrir uma aba nova e digitar o nome à mão.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert "https://www.google.com/search?q=${encodeURIComponent(lead.empresa)}" in script
    assert "site:instagram.com" in script
    assert ">Buscar no Google</a>" in script
    assert ">Buscar no Instagram</a>" in script


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


def test_tela_de_leads_avisa_quando_ha_pendencia_de_contato() -> None:
    # Achado do usuário (17/09/2026): os filtros de prioridade (Ações
    # atrasadas/Sem responsável/Sem próxima ação) já existiam, mas "ninguém
    # usa/conhece" -- ficavam discretos no meio da tela. Banner no topo,
    # só aparece quando há pendência de verdade.
    page = (web_dir / "admin-leads.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "admin-leads.css").read_text(encoding="utf-8")

    assert 'id="lead-attention-banner"' in page
    assert 'id="lead-attention-banner" class="lead-attention-banner" role="status" hidden' in page
    assert "function renderAvisoAtencaoLeads" in script
    assert "function aplicarPrioridade" in script
    assert ".lead-attention-banner" in styles


def test_ficha_do_lead_tem_whatsapp_e_site_de_facil_acesso() -> None:
    # Achado do usuário (17/09/2026): o link de WhatsApp já existia na
    # listagem de leads, mas sumia ao abrir a ficha (só telefone em texto).
    # O site da empresa (já cadastrado) ficava só editável na aba "Empresa",
    # sem virar link em lugar nenhum.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert 'href="https://wa.me/${phoneDigits(lead.telefone)}"' in script
    assert 'id="lead-site-row"' in script
    assert 'id="lead-site-link"' in script
    assert "siteRow.hidden = false;" in script


def test_ficha_do_lead_abre_direto_na_linha_do_tempo() -> None:
    # Achado do usuário (17/09/2026): "histórico de contato confuso" -- a
    # linha do tempo unificada (contatos, mensagens do portal, mudanças de
    # fase, propostas, documentos etc., já montada por
    # app/api/leads.py::timeline_lead) ficava escondida como a 4ª de 7 abas,
    # atrás de "Atendimento Comercial". Passa a ser a aba padrão.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert 'const ABAS_LEAD = [\n    ["timeline", "Linha do tempo"],' in script
    assert 'data-panel="atendimento" hidden' in script
    assert '<div class="lead-tab-panel" data-panel="timeline">' in script


def test_portal_cliente_mostra_jornada_unificada_em_macroetapas() -> None:
    # Item 1 do pedido de melhorias do cliente final (17/09/2026, revisado
    # em 21/09/2026): a jornada linear de 10 passos + o bloco à parte de
    # acompanhamento do INPI viraram uma única jornada de até 5
    # macroetapas por marca/processo, com drawer de sub-eventos ao clicar
    # num nó (app/api/portal_cliente.py::montar_macroetapas).
    script = (web_dir / "static" / "portal-cliente.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "portal-cliente.css").read_text(encoding="utf-8")
    html = (web_dir / "portal-cliente.html").read_text(encoding="utf-8")

    assert "function macroJornada" in script
    assert "function abrirDrawerJornada" in script
    assert "Jornada do Cliente" in script
    assert "function jornadaRegistro" not in script
    assert "function processoTimeline" not in script
    assert "Jornada do atendimento ao registro" not in script
    assert "Acompanhamento oficial no INPI" not in script
    assert 'id="journey-drawer"' in html
    assert ".portal-journey" in styles
    assert ".portal-journey-alerta" in styles
    assert ".portal-drawer-body" in styles
    # Achado do Codex review (PR #90): o link de download só aparecia no
    # drawer da jornada (protocolo/oposição/certificado, e só depois de
    # protocolado) -- a lista completa "Documentos e GRUs" precisa expor o
    # download de qualquer documento com arquivo, incluindo procuração/GRU.
    assert "item.tem_arquivo" in script
    # Achado do usuário (21/09/2026): as bolinhas verdes dos nós da jornada
    # viram o mascote foguete + logo do cliente (mesma imagem do cabeçalho).
    assert "function iconeJornada" in script
    assert "mascote-foguete.png" in script
    assert ".portal-journey-mascote" in styles
    assert ".portal-journey-logo" in styles


def test_portal_cliente_usa_a_mesma_logo_do_painel_interno() -> None:
    # Achado do usuário (17/09/2026, item 3): a logo do portal do cliente
    # era um arquivo fixo, sem relação com a logo cadastrada no painel
    # interno (mecanismo de branding por organização já usado em outras
    # telas públicas). Conecta o portal ao mesmo mecanismo (tenant-branding.js).
    page = (web_dir / "portal-cliente.html").read_text(encoding="utf-8")
    branding_js = (web_dir / "static" / "tenant-branding.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "portal-cliente.css").read_text(encoding="utf-8")

    assert "tenant-branding.js?v=" in page
    assert 'class="brand-avatar"' in page
    # Sem cor customizada, a folha de estilo do tenant ainda deve carregar
    # quando há uma logo própria (senão o filtro de silhueta nunca é removido).
    assert "|| tenant.logo_url" in branding_js
    # A logo grande deste painel não deve herdar o tratamento de avatar
    # circular pequeno (borda/fundo/object-fit) usado no restante do site.
    assert ".portal-brand-panel img.brand-avatar" in styles


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


def test_portal_cliente_exibe_materiais_de_identidade_visual() -> None:
    # Item 2 do pedido de melhorias do cliente final (17/09/2026): área de
    # Identidade Visual por cliente. Escopo definido com o usuário: só a
    # equipe interna cadastra materiais; o cliente só visualiza e baixa no
    # portal (sem formulário de envio nesta tela).
    page = (web_dir / "portal-cliente.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "portal-cliente.js").read_text(encoding="utf-8")

    assert 'id="brand-materials"' in page
    assert "Materiais da marca" in page
    assert 'api("/v1/portal/materiais-marca")' in script
    assert "async function carregarMateriaisMarca" in script
    assert "await carregarMateriaisMarca();" in script


def test_admin_cadastra_materiais_de_identidade_visual_para_o_cliente() -> None:
    # Mesmo item acima, lado administrativo: a equipe cadastra os materiais
    # na ficha do lead (upload + descrição opcional + lista com remoção).
    page = (web_dir / "admin-leads.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert "admin-leads.js?v=" in page
    assert "async function renderMateriaisMarca" in script
    assert 'fetch(`/v1/admin/leads/${lead.id}/materiais-marca`' in script
    assert "renderMateriaisMarca(lead);" in script
    assert "data-remove-material" in script


def test_portal_cliente_exibe_personagem_com_logo_dinamica_do_cliente() -> None:
    # Itens 4/5 do pedido de melhorias do cliente final (17/09/2026):
    # personagem fixo no portal, segurando dinamicamente a logo do cliente
    # (a imagem-base já vem com a mão vazia; a logo é sobreposta via CSS
    # sobre as coordenadas percentuais da mão).
    page = (web_dir / "portal-cliente.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "portal-cliente.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "portal-cliente.css").read_text(encoding="utf-8")

    assert "mascote-foguete.png" in page
    assert 'id="mascote-logo-cliente"' in page
    assert "data.lead.logo_cliente_url" in script
    assert ".portal-mascote-logo" in styles


def test_admin_cadastra_logo_do_cliente_para_o_personagem_do_portal() -> None:
    # Mesmo item acima, lado administrativo: a equipe cadastra a logo do
    # cliente na ficha do lead.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert "async function renderLogoCliente" in script
    assert 'fetch(`/v1/admin/leads/${lead.id}/logo-cliente`' in script
    assert "renderLogoCliente(lead);" in script


def test_upload_de_logo_do_cliente_mostra_criterios_de_tamanho_e_formato() -> None:
    # Achado do usuário (17/09/2026): os critérios de validação da logo
    # (tamanho do arquivo, dimensões, formatos aceitos -- já aplicados no
    # backend via app/api/confiabilidade.py::normalizar_logo) não apareciam
    # em lugar nenhum da tela -- a equipe só descobria o limite ao errar.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "admin-leads.css").read_text(encoding="utf-8")

    assert "lead-logo-cliente-hint" in script
    assert "1 MB" in script
    assert "32×32" in script and "2000×2000" in script
    assert ".lead-logo-cliente-hint" in styles


def test_admin_visualiza_arquivos_enviados_pelo_cliente_no_portal() -> None:
    # Achado da validação do Portal do Cliente (17/09/2026): documentos
    # enviados pelo cliente (ArquivoClientePortal) não tinham NENHUMA tela
    # administrativa equivalente -- a equipe não conseguia ver nem baixar o
    # que o cliente enviava pelo portal.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert "async function renderPortalArquivos" in script
    assert 'fetch(`/v1/admin/leads/${lead.id}/portal-arquivos`)' in script
    assert "renderPortalArquivos(lead);" in script


def test_funil_do_lead_nao_marca_etapa_pulada_como_concluida() -> None:
    # Achado do usuário (17/09/2026): o funil marcava toda etapa anterior à
    # fase atual como "concluída" (✓) só pela posição na sequência -- um
    # lead movido manualmente direto para "Ganho" mostrava "Pagamento
    # confirmado" como concluído mesmo sem nunca ter existido nenhuma
    # contratação financeira por trás (caso real: lead "Tactical Cloud").
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")
    styles = (web_dir / "static" / "styles.css").read_text(encoding="utf-8")

    assert "const alcancada = i === 0 || Boolean(datas[f]);" in script
    assert '(alcancada ? "done" : "skipped")' in script
    assert ".lfs.skipped .lfs-dot" in styles
    assert ".lfs.skipped .lfs-label" in styles


def test_salvar_fase_do_lead_mostra_motivo_real_do_erro() -> None:
    # Achado da varredura ampla do sistema (18/09/2026): o botão "Salvar
    # fase" só mostrava "Erro — tentar de novo" genérico, sem o motivo real
    # -- quem tentava mover um lead pra "Ganho" sem contratação financeira
    # vinculada (trava do PR #61) não fazia ideia do porquê estava travado.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert 'id="lead-fase-status"' in script
    assert "erro.detail || \"Não foi possível salvar a fase.\"" in script
    assert '"Erro — tentar de novo"' not in script


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


def test_crm_abre_contato_usa_a_mesma_tela_do_lead() -> None:
    # Achado do usuário (21/09/2026): "Abrir contato" no Kanban/histórico do
    # CRM tinha que abrir "exatamente a mesma tela" do lead -- em vez de
    # duplicar a lógica enorme e interligada de admin-leads.js (lista e
    # diálogo compartilham estado, permissões e funções auxiliares), a
    # página do CRM passou a carregar aquele script inteiro e reaproveitar
    # openLead() dele direto, com uma cópia oculta do HTML que ele espera
    # encontrar (filtros, métricas, tabela, paginação) pra não quebrar.
    page = (web_dir / "admin-crm.html").read_text(encoding="utf-8")
    script = (web_dir / "static" / "admin-crm.js").read_text(encoding="utf-8")

    assert '/static/admin-leads.js?v=72" defer' in page
    assert '/static/admin-leads.css?v=31"' in page
    # O diálogo do lead de verdade (não uma cópia reduzida) fica visível.
    assert '<dialog id="lead-dialog" class="lead-dialog">' in page
    assert 'id="lead-dialog-content"' in page
    # Cópia oculta do que admin-leads.js precisa pra não quebrar ao carregar
    # nesta página (ver comentário no próprio admin-crm.html).
    assert '<div hidden aria-hidden="true">' in page
    assert 'id="leads-list"' in page
    assert 'id="lead-filters"' in page

    assert "openLead(Number(gatilho.dataset.openContact));" in script
    # Achado: admin-leads.js só fecha o diálogo com querySelector(".dialog-close")
    # (o primeiro da página) -- como o CRM já tinha outros dois antes dele no
    # DOM (lembrete/adiar), o close do #lead-dialog precisa de handler próprio.
    assert 'id="close-lead-dialog"' in page
    assert '#close-lead-dialog' in script

    # Achado: os dois scripts declaravam `const message` no mesmo escopo
    # global (scripts sem type=module compartilham o escopo) -- quebrava
    # com SyntaxError assim que os dois carregavam juntos na mesma página.
    assert "const crmMessage = " in script
    assert "const message = " not in script

    # Achados do Codex review (PR #96):
    # 1) salvar atendimento/mover fase dentro do diálogo reaproveitado só
    #    recarregava a lista oculta de Leads -- Kanban e histórico do CRM
    #    ficavam desatualizados até um refresh manual.
    assert 'addEventListener("close", () => {' in script
    assert "loadKanban().catch" in script
    assert "loadHistory().catch" in script
    # 2) #admin-message (onde admin-leads.js escreve confirmações/erros de
    #    ações do diálogo) estava dentro do bloco oculto -- o operador nunca
    #    via essas mensagens. Agora fica visível dentro do próprio diálogo.
    assert '<div id="admin-message" class="status-message lead-dialog-message" role="status"></div>' in page
    assert page.index('id="admin-message"') < page.index('<div hidden aria-hidden="true">')


def test_lead_permite_vincular_processo_direto_do_aviso() -> None:
    # Achado do usuário (21/09/2026): o aviso "processo não vinculado" só
    # mandava o operador pra tela de Processos monitorados -- de dentro da
    # ficha do lead não tinha como vincular. Agora dá pra digitar o número
    # do processo (já cadastrado na carteira) direto no aviso e vincular.
    script = (web_dir / "static" / "admin-leads.js").read_text(encoding="utf-8")

    assert "data-vincular-processo" in script
    assert "data-processo-numero" in script
    assert '/v1/admin/carteira?busca=${encodeURIComponent(numero)}' in script
    assert "{ lead_id: Number(form.dataset.leadId) }" in script
    # Sem o processo já cadastrado na carteira, o aviso orienta a cadastrar
    # lá primeiro em vez de falhar silenciosamente.
    assert "Cadastre-o lá primeiro" in script


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


def test_card_de_prospeccao_mostra_descricao_do_cnae() -> None:
    # Achado do usuário (20/09/2026): a tela de Prospecção mostrava só o
    # código do CNAE (ex.: "CNAE 4711302"), sem a equipe de atendimento
    # saber o que cada número significa -- descrição vem do backend
    # (app/cnae.py, tabela oficial do CNAE 2.0) via cnae_principal_descricao.
    script = (web_dir / "static" / "admin-prospeccao.js").read_text(encoding="utf-8")

    assert "item.cnae_principal_descricao" in script


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

    assert "admin-crm-overrides.css?v=8" in page
    assert "admin-regras-automaticas.js?v=5" in page
    assert 'includes("crm.manage")' in script
    assert 'includes("leads.manage")' not in script
    assert "politicaErrorDetail" in script
    assert 'type="checkbox"' in styles
    assert "height: 18px" in styles
    assert "width: 18px" in styles

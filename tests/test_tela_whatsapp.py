import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import obter_usuario_atual
from app.main import app
from tests.conftest import auth_override

# Tela de consentimentos e fila do WhatsApp (/admin/configuracao/whatsapp). O navegador não roda na CI
# Python; estes testes garantem que o HTML, o JS e o menu continuam coerentes entre si e com a CSP.

WEB = Path("app/web")
HTML = (WEB / "admin-whatsapp.html").read_text(encoding="utf-8")
JS = (WEB / "static" / "admin-whatsapp.js").read_text(encoding="utf-8")
SHELL = (WEB / "static" / "admin-shell.js").read_text(encoding="utf-8")


def test_todo_id_usado_pelo_script_existe_na_pagina() -> None:
    usados = set(re.findall(r'querySelector\("#([\w-]+)"\)', JS)) | set(re.findall(r'"#(wa-[\w-]+)"', JS))
    assert usados, "o script deveria consultar elementos da página"
    for identificador in usados:
        assert f'id="{identificador}"' in HTML, identificador


def test_pagina_respeita_a_csp_sem_codigo_ou_estilo_inline() -> None:
    assert "style=" not in HTML
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", HTML)
    assert not re.search(r"\son(click|change|submit|load)=", HTML)
    assert "<style" not in HTML


def test_menu_e_pagina_usam_o_mesmo_identificador_de_secao() -> None:
    assert 'data-admin-section="config-whatsapp"' in HTML
    assert 'id: "config-whatsapp"' in SHELL and 'href: "/admin/configuracao/whatsapp"' in SHELL


def test_scripts_e_estilo_da_pagina_existem() -> None:
    for caminho in re.findall(r'(?:src|href)="/static/([\w.-]+)\?v=\d+"', HTML):
        assert (WEB / "static" / caminho).exists(), caminho


def test_script_nao_monta_html_com_campo_da_api_sem_escapar() -> None:
    # Campos das linhas vindas da API (c.* e m.*) só entram no HTML por esc(); a data vai por dataCurta()
    # e a situação por selo(), que escapam por dentro.
    assert "const esc = " in JS
    crus = re.findall(r"\$\{[cm]\.[\w.]+\}", JS)
    assert crus == [], crus


def test_rota_da_pagina_exige_permissao_e_responde() -> None:
    app.dependency_overrides[obter_usuario_atual] = auth_override()
    try:
        resposta = TestClient(app).get("/admin/configuracao/whatsapp")
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    assert "Consentimentos" in resposta.text


def test_controles_de_alteracao_ficam_ocultos_ate_confirmar_a_permissao_de_gestao() -> None:
    # Revisão do Codex (PR #202): quem só lê não deve ver formulários que sempre resultariam em 403.
    assert re.search(r'id="wa-secao-registrar"[^>]*\bhidden\b', HTML)
    assert re.search(r'id="wa-secao-revogar"[^>]*\bhidden\b', HTML)
    assert '"leads.manage"' in JS and "podeGerir &&" in JS


def test_menu_usa_a_mesma_permissao_do_menu_pai_para_nao_ficar_inalcancavel() -> None:
    # Revisão do Codex (PR #202): o pai "Configuração" exige leads.view; o item filho não pode exigir só crm.view.
    entrada = re.search(r'\{ id: "config-whatsapp"[^}]*\}', SHELL).group(0)
    assert 'permission: "leads.view"' in entrada and "crm.view" not in entrada

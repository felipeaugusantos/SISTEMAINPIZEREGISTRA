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


def test_script_nao_monta_html_com_dados_sem_escapar() -> None:
    # Todo valor vindo da API entra no HTML por esc(); a única exceção são os selos, que escapam por dentro.
    assert "const esc = " in JS
    crus = re.findall(r"\$\{(?!esc\(|selo\(|dataCurta\(|FINALIDADES|MOTIVOS|c\.status ===|c\.base_legal ===|m\.status ===)[^}]+\}", JS)
    assert crus == [], crus


def test_rota_da_pagina_exige_permissao_e_responde() -> None:
    app.dependency_overrides[obter_usuario_atual] = auth_override()
    try:
        resposta = TestClient(app).get("/admin/configuracao/whatsapp")
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    assert "Consentimentos" in resposta.text

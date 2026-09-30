"""Fase 19.4 (white-label): a consulta pública (início, busca gratuita,
relatório, processo) e as páginas que a acompanham (sobre, contato,
privacidade, portal do cliente) trocam "Zé Registra" pelo nome do
escritório dono do domínio -- decisão do usuário (29/09/2026): a consulta
pública fica disponível para os outros escritórios, com o nome deles. O site
institucional de cada escritório é responsabilidade dele e fica fora.
"""

from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "app" / "web"
PAGINAS = ("index", "buscar-gratuita", "relatorio", "processo", "sobre", "contato", "privacidade", "portal-cliente")


def test_paginas_da_consulta_publica_carregam_o_script_de_marca() -> None:
    for pagina in PAGINAS:
        html = (WEB / f"{pagina}.html").read_text(encoding="utf-8")
        assert "/static/tenant-branding.js?v=8" in html, pagina


def test_script_troca_o_nome_da_plataforma_so_com_marca_propria() -> None:
    script = (WEB / "static" / "tenant-branding.js").read_text(encoding="utf-8")
    assert "function aplicarNomeDoEscritorio(marca)" in script
    assert "if (!marca?.propria || !marca.nome) return;" in script
    assert "aplicarNomeDoEscritorio(tenant.marca)" in script
    # Conteúdo montado depois pelos scripts das páginas (relatório, processo).
    assert "new MutationObserver" in script
    # Proteção contra laço quando o nome do escritório contém a marca.
    assert "if (contemPlataforma) return;" in script

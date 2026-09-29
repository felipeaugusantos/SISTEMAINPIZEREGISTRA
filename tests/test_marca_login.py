"""Fase 19.2 (white-label): as telas de login, MFA e recuperação de senha
mostram a marca do escritório dono do domínio. Antes tinham "Zé Registra®",
o personagem e o logotipo da plataforma fixos, para qualquer domínio.
"""

from pathlib import Path

from app.marca import css_marca

WEB = Path(__file__).resolve().parents[1] / "app" / "web"
TELAS = ("login", "esqueci-senha", "redefinir-senha", "configurar-mfa", "alterar-senha")


def test_telas_de_autenticacao_carregam_o_script_que_aplica_a_marca() -> None:
    script = (WEB / "static" / "auth-pages.js").read_text(encoding="utf-8")
    assert "async function aplicarMarcaDoDominio" in script
    assert 'fetch("/v1/tenant/branding")' in script
    assert "/v1/tenant/branding.css" in script
    for tela in TELAS:
        html = (WEB / f"{tela}.html").read_text(encoding="utf-8")
        assert "/static/auth-pages.js?v=6" in html, tela


def test_css_da_marca_libera_a_logo_propria_nas_telas_de_autenticacao() -> None:
    css = css_marca({"logo_url": "/v1/tenant/logo"})
    assert ".auth-brand img" in css
    assert ".ops-login-shell .portal-brand-panel>img" in css


def test_cor_do_escritorio_chega_aos_controles_das_telas_de_autenticacao() -> None:
    # Revisão do Codex no PR #154: só --forest não era usado por essas telas.
    css = css_marca({"cor_primaria": "#123abc"})
    assert "--portal-primary:#123abc" in css
    assert ".auth-card button,.auth-card .primary-button{background:#123abc}" in css
    assert "--portal-primary" not in css_marca({"cor_primaria": "#123abc"}, painel=True)


def test_sem_logo_propria_nao_mexe_no_visual_das_telas() -> None:
    assert ".auth-brand" not in css_marca({"cor_primaria": "#123abc"})

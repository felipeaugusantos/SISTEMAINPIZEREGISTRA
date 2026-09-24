from types import SimpleNamespace

from app.api.email_leads_config import DEFAULTS, config_email_leads, substituir_placeholders_organizacao

# --- Achado da Fase 15.2 (auditoria fina de Leads, 23/09/2026): o
# template padrão tinha a marca "Zé Registra" e a assinatura "Letícia"
# hardcoded, com placeholders literais ("[Sobrenome]",
# "[Telefone/WhatsApp]", "[E-mail]", "[Site]") que nunca eram
# substituídos -- se uma organização-tenant não customizasse o próprio
# modelo, esse texto ia direto pro cliente com a marca ERRADA. ---


def test_template_padrao_nao_tem_marca_nem_assinatura_hardcoded() -> None:
    assert "Zé Registra" not in DEFAULTS["assunto"]
    assert "Zé Registra" not in DEFAULTS["corpo"]
    assert "Letícia" not in DEFAULTS["corpo"]
    assert "[Sobrenome]" not in DEFAULTS["corpo"]
    assert "[Telefone/WhatsApp]" not in DEFAULTS["corpo"]
    assert "{{organizacao.nome}}" in DEFAULTS["corpo"]


def test_substituir_placeholders_organizacao_usa_dados_reais() -> None:
    org = SimpleNamespace(
        nome="Acme Marcas", telefone_contato="(11) 4000-0000", email_contato="contato@acme.com.br", branding={}
    )
    texto = "{{organizacao.nome}} | {{organizacao.telefone}} | {{organizacao.email}} | {{organizacao.site}}"

    resultado = substituir_placeholders_organizacao(texto, org)

    assert resultado == "Acme Marcas | (11) 4000-0000 | contato@acme.com.br | "


def test_substituir_placeholders_organizacao_cai_para_branding_quando_sem_contato_direto() -> None:
    org = SimpleNamespace(
        nome="Acme Marcas",
        telefone_contato=None,
        email_contato=None,
        branding={"telefone": "(11) 5000-0000", "email": "branding@acme.com.br", "site": "acme.com.br"},
    )
    texto = "{{organizacao.telefone}} {{organizacao.email}} {{organizacao.site}}"

    resultado = substituir_placeholders_organizacao(texto, org)

    assert resultado == "(11) 5000-0000 branding@acme.com.br acme.com.br"


def test_substituir_placeholders_organizacao_nunca_deixa_placeholder_cru() -> None:
    # Sem organização (ou dados ausentes), o placeholder vira string vazia,
    # nunca o texto literal "{{organizacao.*}}" visível pro cliente.
    resultado = substituir_placeholders_organizacao("Fale conosco: {{organizacao.telefone}}", None)
    assert "{{organizacao" not in resultado


def test_config_email_leads_sem_organizacao_devolve_padrao() -> None:
    assert config_email_leads(None) == DEFAULTS

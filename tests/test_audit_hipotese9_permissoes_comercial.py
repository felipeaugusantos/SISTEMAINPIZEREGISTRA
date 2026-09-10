"""Auditoria técnica (10/09/2026) — Hipótese 9: permissões do perfil
"comercial" (app/permissions.py) -- mapeamento exato e avaliação pelo
princípio do menor privilégio.

Correção P1 aplicada em 10/09/2026: confirmado com o negócio que ninguém
no perfil "comercial" de fato edita financeiro/jurídico/carteira -- só
consulta para dar contexto no atendimento. portfolio.manage, legal.manage,
finance.manage e finance.export foram removidas do perfil; a visualização
(portfolio.view, legal.view, finance.view) foi mantida.
"""

from app.permissions import permissoes_do_perfil


def test_permissoes_exatas_do_perfil_comercial() -> None:
    """CORRIGIDO: snapshot exato do perfil após a redução de escopo -- se
    alguém alterar o perfil sem querer, este teste quebra e chama atenção
    para a mudança."""
    permissoes = permissoes_do_perfil("comercial")

    assert permissoes == frozenset(
        {
            "dashboard.view",
            "leads.view",
            "leads.pii.view",
            "leads.manage",
            "leads.export",
            "crm.view",
            "crm.manage",
            "prospeccao.view",
            "prospeccao.manage",
            "prospeccao.convert",
            "prospeccao.export",
            "portfolio.view",
            "legal.view",
            "finance.view",
            "risk.view",
        }
    )


def test_comercial_nao_pode_mais_gerenciar_carteira_juridico_e_financeiro() -> None:
    """CORRIGIDO (achado H9/P1): portfolio.manage, legal.manage,
    finance.manage e finance.export foram removidas do perfil comercial --
    nenhuma dessas quatro permissões está ligada a atendimento comercial no
    sentido estrito (leads/CRM/prospecção); são módulos operacionais
    distintos que ninguém no perfil de fato editava."""
    permissoes = permissoes_do_perfil("comercial")

    assert "portfolio.manage" not in permissoes
    assert "legal.manage" not in permissoes
    assert "finance.manage" not in permissoes
    assert "finance.export" not in permissoes


def test_comercial_mantem_visualizacao_de_carteira_juridico_e_financeiro() -> None:
    """Visualização preservada -- o vendedor continua vendo status de
    pagamento, prazos jurídicos e processos do cliente para dar contexto no
    atendimento, só perde o poder de editar esses dados."""
    permissoes = permissoes_do_perfil("comercial")

    assert "portfolio.view" in permissoes
    assert "legal.view" in permissoes
    assert "finance.view" in permissoes


def test_comercial_visualiza_dados_pessoais_completos() -> None:
    """CONFIRMADO: leads.pii.view está no perfil -- todo usuário comercial
    vê e-mail/telefone/documento sem máscara (ver _mascarar_email/
    _mascarar_telefone/_mascarar_documento em app/api/leads.py, só
    aplicados quando a permissão FALTA)."""
    assert "leads.pii.view" in permissoes_do_perfil("comercial")


def test_comercial_nao_pode_arquivar_leads_nem_administrar_usuarios() -> None:
    """CONFIRMADO: leads.delete (arquivar/restaurar contato) e
    users.manage NÃO estão no perfil comercial -- essas duas ações ficam
    de fato restritas a perfis mais amplos (administrador, ceo, tech,
    supervisor)."""
    permissoes = permissoes_do_perfil("comercial")

    assert "leads.delete" not in permissoes
    assert "users.manage" not in permissoes

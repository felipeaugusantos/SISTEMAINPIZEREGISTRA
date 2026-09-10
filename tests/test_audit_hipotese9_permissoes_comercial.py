"""Auditoria técnica (10/09/2026) — Hipótese 9: permissões do perfil
"comercial" (app/permissions.py) -- mapeamento exato e avaliação pelo
princípio do menor privilégio. Não altera nenhuma permissão.
"""

from app.permissions import permissoes_do_perfil


def test_permissoes_exatas_do_perfil_comercial() -> None:
    """CONFIRMADO por leitura direta de PERFIS["comercial"]
    (app/permissions.py). Snapshot exato -- se alguém alterar o perfil
    sem querer, este teste quebra e chama atenção para a mudança."""
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
            "portfolio.manage",
            "legal.view",
            "legal.manage",
            "finance.view",
            "finance.manage",
            "finance.export",
            "risk.view",
        }
    )


def test_comercial_pode_gerenciar_carteira_e_dados_juridicos_e_financeiros() -> None:
    """CONFIRMADO: o perfil "comercial" tem portfolio.manage, legal.manage,
    finance.manage e finance.export -- pode criar/alterar processos
    monitorados, dados jurídicos e lançamentos financeiros, além de
    exportar dados financeiros. Nenhuma dessas quatro permissões está
    ligada a atendimento comercial no sentido estrito (leads/CRM/
    prospecção); são módulos operacionais distintos."""
    permissoes = permissoes_do_perfil("comercial")

    assert "portfolio.manage" in permissoes
    assert "legal.manage" in permissoes
    assert "finance.manage" in permissoes
    assert "finance.export" in permissoes


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

"""Marca (identidade visual) de cada escritório -- Fase 19, white-label.

Outros escritórios vão usar o sistema, cada um com domínio próprio
(decisão do usuário, 29/09/2026). A marca de uma organização vem de
``Organizacao.branding`` (nome_exibido, cor_primaria, logo_url -- editáveis
em Confiabilidade e LGPD). Funções puras, reaproveitadas pelo painel
(/v1/auth/me) e pelas folhas de estilo servidas como recurso 'self' (o CSP
``style-src 'self'`` bloqueia estilo inline).
"""

import re

from app.models import Organizacao
from app.settings import get_settings

COR_HEX_VALIDA = re.compile(r"^#[0-9a-fA-F]{6}$")


def cor_valida(cor: object) -> str | None:
    return cor if isinstance(cor, str) and COR_HEX_VALIDA.fullmatch(cor) else None


def marca_organizacao(org: Organizacao) -> dict:
    """Nome, logo e cor do escritório. ``propria`` indica se há algo a
    aplicar sobre a identidade padrão da plataforma: a organização padrão
    sem nada configurado continua com a aparência de sempre."""
    branding = org.branding or {}
    nome_exibido = branding.get("nome_exibido")
    logo_url = branding.get("logo_url")
    cor = cor_valida(branding.get("cor_primaria"))
    padrao = org.slug == get_settings().default_organization_slug
    return {
        "nome": nome_exibido or org.nome,
        "logo_url": logo_url,
        "cor_primaria": cor,
        "propria": bool(nome_exibido or logo_url or cor or not padrao),
    }


def css_marca(branding: dict | None, *, painel: bool = False) -> str:
    """CSS da marca: cor principal e, com logo própria, sem o filtro que
    transforma o personagem padrão em silhueta branca."""
    branding = branding or {}
    partes = []
    cor = cor_valida(branding.get("cor_primaria"))
    if cor:
        partes.append(f":root{{--forest:{cor}}}")
        if not painel:
            # Revisão do Codex no PR #154: o login e o portal usam as
            # variáveis --portal-* (portal-cliente.css) e as demais telas de
            # autenticação têm a cor do botão fixa em styles.css -- só
            # --forest não chegava a nenhum controle visível.
            partes.append(f":root{{--portal-primary:{cor};--portal-primary-deep:{cor};--portal-accent:{cor}}}")
            partes.append(f".auth-card button,.auth-card .primary-button{{background:{cor}}}")
    if branding.get("logo_url"):
        partes.append(".portal-brand-panel img.brand-avatar{filter:none}")
        # Telas de login/recuperação de senha (Fase 19.2).
        partes.append(
            ".auth-brand img,.ops-login-shell .portal-brand-panel>img{filter:none;object-fit:contain}"
        )
        if painel:
            partes.append(
                ".admin-sidebar-brand img.brand-avatar,.admin-mobile-header img.brand-avatar"
                "{filter:none;object-fit:contain}"
            )
    return "".join(partes)

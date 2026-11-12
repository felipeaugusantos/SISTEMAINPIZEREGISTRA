"""Link público estável e revogável para o relatório de uma pesquisa.

O token HMAC de app.public_report_tokens dura 15 minutos e serve ao fluxo
da tela, logo após o envio do formulário. O e-mail de confirmação precisa
de um link que continue válido depois disso -- e que possa ser desligado
sem derrubar os demais. Este módulo segue o mesmo padrão de
PropostaComercial.public_token_hash: token opaco aleatório, só o hash
(SHA-256, app.auth.hash_token) fica na base, com expiração própria;
revogar é limpar os campos da pesquisa.
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_token
from app.models import PesquisaMarca

# O prefixo distingue o link estável do token HMAC nos logs e facilita
# identificar a origem de um acesso negado.
PREFIXO_TOKEN_LINK = "rlk_"


def gerar_token_link() -> str:
    return f"{PREFIXO_TOKEN_LINK}{secrets.token_urlsafe(32)}"


def definir_link_relatorio(pesquisa: PesquisaMarca, *, ttl_horas: int) -> str:
    """Gera um novo link estável para a pesquisa e o registra (hash + expiração).

    Substitui qualquer link anterior: o hash antigo deixa de valer no
    próximo commit, o que equivale a revogá-lo.
    """
    token = gerar_token_link()
    pesquisa.relatorio_link_token_hash = hash_token(token)
    pesquisa.relatorio_link_expira_em = datetime.now(UTC) + timedelta(hours=max(1, ttl_horas))
    return token


def revogar_link_relatorio(pesquisa: PesquisaMarca) -> None:
    """Desliga o link estável da pesquisa (efetivo no próximo commit)."""
    pesquisa.relatorio_link_token_hash = None
    pesquisa.relatorio_link_expira_em = None


async def validar_token_link(
    session: AsyncSession,
    pesquisa_id: str,
    organizacao_id: int,
    token: str,
) -> bool:
    """Confere o token opaco contra o hash gravado, o tenant e a expiração."""
    if not token.startswith(PREFIXO_TOKEN_LINK) or len(token) > 128:
        return False
    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if pesquisa is None or not pesquisa.relatorio_link_token_hash:
        return False
    if not secrets.compare_digest(pesquisa.relatorio_link_token_hash, hash_token(token)):
        return False
    expira_em = pesquisa.relatorio_link_expira_em
    if expira_em is None:
        return False
    if expira_em.tzinfo is None:
        expira_em = expira_em.replace(tzinfo=UTC)
    return expira_em > datetime.now(UTC)

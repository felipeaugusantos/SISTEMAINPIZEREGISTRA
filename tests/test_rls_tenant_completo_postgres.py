"""Teste de segurança (30/09/2026): garante que as tabelas por escritório que
estavam sem RLS completo passaram a ter isolamento no banco.

Achado do pentest: 3 tabelas com organizacao_id estavam com RLS totalmente
desligado (o inpi_app, que tem DML nelas, via linhas de todos os escritórios)
e 11 tinham RLS sem FORCE. A migration zf41d5b2c813 corrigiu. Este teste roda
contra o Postgres real (pula se indisponível), no mesmo padrão dos demais
testes de RLS.
"""

import os

import asyncpg
import pytest

ADMIN_DSN = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")

TABELAS_RLS_COMPLETO = ("assinaturas_documentos_lead", "versoes_documentos_lead", "modelos_ranking_busca")
TABELAS_FORCE = (
    "ativos_partes_pi",
    "ativos_pi",
    "ativos_processos_pi",
    "centros_custo_financeiros",
    "contratos_juridicos",
    "custos_juridicos",
    "departamentos_financeiros",
    "documentos_ativos_pi",
    "fornecedores_juridicos",
    "projetos_busca_marca",
    "webhooks_financeiros",
)


async def _conectar() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(ADMIN_DSN)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL real indisponivel: {type(exc).__name__}")


@pytest.mark.asyncio
@pytest.mark.parametrize("tabela", TABELAS_RLS_COMPLETO)
async def test_tabela_por_escritorio_tem_rls_forcado_e_politica(tabela: str) -> None:
    conn = await _conectar()
    try:
        linha = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1", tabela
        )
        politicas = await conn.fetchval("SELECT count(*) FROM pg_policies WHERE tablename = $1", tabela)
    finally:
        await conn.close()
    assert linha["relrowsecurity"] is True, f"{tabela} deveria ter RLS habilitado"
    assert linha["relforcerowsecurity"] is True, f"{tabela} deveria ter RLS forçado"
    assert politicas >= 1, f"{tabela} deveria ter política de isolamento"


@pytest.mark.asyncio
@pytest.mark.parametrize("tabela", TABELAS_FORCE)
async def test_tabela_com_rls_agora_esta_forcada(tabela: str) -> None:
    conn = await _conectar()
    try:
        forcado = await conn.fetchval("SELECT relforcerowsecurity FROM pg_class WHERE relname = $1", tabela)
    finally:
        await conn.close()
    assert forcado is True, f"{tabela} deveria ter RLS forçado (FORCE)"

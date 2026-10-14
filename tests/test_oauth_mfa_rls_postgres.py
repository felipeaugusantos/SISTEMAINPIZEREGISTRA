import asyncio
import os
from urllib.parse import quote, urlsplit
from uuid import uuid4

import asyncpg
import pytest

# Revisão do Codex no PR #186: o desafio do MFA social precisa poder ser consumido pela role restrita
# da aplicação sob RLS. O UPDATE ... SET mfa_token_hash = NULL é recusado pelo PostgreSQL (a política
# de SELECT também vale para a linha NOVA de um UPDATE que lê colunas); o DELETE condicional funciona
# e é atômico. FakeSession não enxerga RLS, por isso este teste usa o PostgreSQL real.

ADMIN_DSN = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")

DELETE_CONDICIONAL = "DELETE FROM tentativas_oauth WHERE id = $1 AND mfa_token_hash = $2"


async def _conectar(dsn: str) -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL real indisponivel para teste RLS: {type(exc).__name__}")


async def _criar_role_aplicacao(admin: asyncpg.Connection) -> tuple[str, str]:
    role = f"rls_oauth_{uuid4().hex[:12]}"
    senha = uuid4().hex + uuid4().hex
    await admin.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{senha}' INHERIT")
    await admin.execute(f'GRANT inpi_app TO "{role}"')
    origem = urlsplit(ADMIN_DSN)
    banco = origem.path.lstrip("/") or "inpi"
    dsn = f"postgresql://{quote(role)}:{quote(senha)}@{origem.hostname or 'localhost'}:{origem.port or 5432}/{banco}"
    return role, dsn


async def _inserir_tentativa(admin: asyncpg.Connection) -> tuple[int, str]:
    token_hash = uuid4().hex + uuid4().hex
    tentativa_id = await admin.fetchval(
        """
        INSERT INTO tentativas_oauth
            (provedor, state_hash, browser_token_hash, nonce, code_verifier, modo, destino,
             mfa_token_hash, expira_em)
        VALUES ('google', $1, 'browser', 'nonce', 'verifier', 'login', '/admin', $2,
                now() + interval '10 minutes')
        RETURNING id
        """,
        uuid4().hex,
        token_hash,
    )
    return tentativa_id, token_hash


async def _contexto_oauth_mfa(conexao: asyncpg.Connection, valor: str) -> None:
    await conexao.execute(
        "SELECT set_config('app.auth_scope', 'oauth_mfa', true), set_config('app.auth_value', $1, true), "
        "set_config('app.auth_aux', '', true), set_config('app.superadmin', 'false', true)",
        valor,
    )


async def _consumir(dsn: str, tentativa_id: int, token_hash: str, contexto: str | None = None) -> str:
    conexao = await _conectar(dsn)
    try:
        async with conexao.transaction():
            await _contexto_oauth_mfa(conexao, contexto or token_hash)
            return await conexao.execute(DELETE_CONDICIONAL, tentativa_id, token_hash)
    finally:
        await conexao.close()


async def test_desafio_mfa_e_consumido_uma_unica_vez_sob_rls() -> None:
    admin = await _conectar(ADMIN_DSN)
    role = None
    ids: list[int] = []
    try:
        role, dsn_app = await _criar_role_aplicacao(admin)
        propria, hash_propria = await _inserir_tentativa(admin)
        alheia, hash_alheia = await _inserir_tentativa(admin)
        ids = [propria, alheia]

        # Contexto de OUTRO desafio não enxerga nem apaga a linha: o RLS continua valendo.
        assert await _consumir(dsn_app, propria, hash_propria, contexto=hash_alheia) == "DELETE 0"
        # O desafio da própria pessoa é consumido pela role restrita.
        assert await _consumir(dsn_app, propria, hash_propria) == "DELETE 1"
        # Já consumido: uma segunda conclusão com o mesmo token não afeta nenhuma linha.
        assert await _consumir(dsn_app, propria, hash_propria) == "DELETE 0"
        # A linha de outro desafio permanece intacta.
        assert await admin.fetchval("SELECT mfa_token_hash FROM tentativas_oauth WHERE id = $1", alheia) == hash_alheia
    finally:
        if ids:
            await admin.execute("DELETE FROM tentativas_oauth WHERE id = ANY($1::bigint[])", ids)
        if role:
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


async def test_duas_conclusoes_simultaneas_so_uma_consome_o_desafio() -> None:
    admin = await _conectar(ADMIN_DSN)
    role = None
    ids: list[int] = []
    try:
        role, dsn_app = await _criar_role_aplicacao(admin)
        tentativa, token_hash = await _inserir_tentativa(admin)
        ids = [tentativa]
        resultados = await asyncio.gather(*(_consumir(dsn_app, tentativa, token_hash) for _ in range(5)))
        assert sorted(resultados) == ["DELETE 0"] * 4 + ["DELETE 1"]
    finally:
        if ids:
            await admin.execute("DELETE FROM tentativas_oauth WHERE id = ANY($1::bigint[])", ids)
        if role:
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()

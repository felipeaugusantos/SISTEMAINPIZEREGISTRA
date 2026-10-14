import os
from urllib.parse import quote, urlsplit
from uuid import uuid4

import asyncpg
import pytest

# Revisão do Codex no PR #186: a política de UPDATE de tentativas_oauth (RLS) precisa aceitar a
# transição terminal do desafio MFA (mfa_token_hash -> NULL) sem afrouxar quem pode atualizar.
# FakeSession não enxerga RLS; este teste usa o PostgreSQL real com a role restrita da aplicação.

ADMIN_DSN = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")


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


async def test_desafio_mfa_pode_ser_consumido_e_nao_afeta_outras_linhas() -> None:
    admin = await _conectar(ADMIN_DSN)
    role = None
    ids: list[int] = []
    try:
        role, dsn_app = await _criar_role_aplicacao(admin)
        propria, hash_propria = await _inserir_tentativa(admin)
        alheia, hash_alheia = await _inserir_tentativa(admin)
        ids = [propria, alheia]
        app = await _conectar(dsn_app)
        try:
            # Valor de contexto de OUTRO desafio não atualiza a linha: o USING continua valendo.
            async with app.transaction():
                await _contexto_oauth_mfa(app, hash_alheia)
                status = await app.execute(
                    "UPDATE tentativas_oauth SET mfa_token_hash = NULL WHERE id = $1", propria
                )
            assert status == "UPDATE 0"

            # O desafio da própria pessoa pode ser consumido (token -> NULL) pela role restrita.
            try:
                async with app.transaction():
                    await _contexto_oauth_mfa(app, hash_propria)
                    status = await app.execute(
                        "UPDATE tentativas_oauth SET mfa_token_hash = NULL WHERE id = $1 AND mfa_token_hash = $2",
                        propria,
                        hash_propria,
                    )
            except asyncpg.InsufficientPrivilegeError as erro:
                politicas = await admin.fetch(
                    "SELECT policyname, cmd, qual, with_check FROM pg_policies WHERE tablename = 'tentativas_oauth'"
                )
                raise AssertionError(f"RLS recusou a transição: {erro}; políticas: {[dict(p) for p in politicas]}") from erro
            assert status == "UPDATE 1"

            # Já consumido: uma segunda conclusão com o mesmo token não afeta nenhuma linha.
            async with app.transaction():
                await _contexto_oauth_mfa(app, hash_propria)
                status = await app.execute(
                    "UPDATE tentativas_oauth SET mfa_token_hash = NULL WHERE id = $1 AND mfa_token_hash = $2",
                    propria,
                    hash_propria,
                )
            assert status == "UPDATE 0"
        finally:
            await app.close()
        assert await admin.fetchval("SELECT mfa_token_hash FROM tentativas_oauth WHERE id = $1", alheia) == hash_alheia
    finally:
        if ids:
            await admin.execute("DELETE FROM tentativas_oauth WHERE id = ANY($1::bigint[])", ids)
        if role:
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()

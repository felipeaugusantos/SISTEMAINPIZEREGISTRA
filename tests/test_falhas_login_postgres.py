import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import BLOQUEIO_LOGIN, LIMITE_FALHAS_LOGIN, registrar_falha_login
from app.models import UsuarioOperacoes

# Auditoria externa de 14/10/2026, S05: contar falhas de login lendo e somando em Python perdia
# tentativas quando as requisições eram simultâneas. A contagem e o bloqueio saem de um UPDATE
# atômico no banco; FakeSession não reproduz concorrência, por isso estes testes usam PostgreSQL real.

ADMIN_DSN = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")


async def _conectar() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(ADMIN_DSN)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL real indisponivel: {type(exc).__name__}")


async def _criar_usuario(admin: asyncpg.Connection, sufixo: str) -> tuple[int, int]:
    plano_id = await admin.fetchval("SELECT id FROM planos_saas ORDER BY id LIMIT 1")
    if plano_id is None:
        pytest.skip("Base de integracao precisa de ao menos um plano SaaS")
    org_id = await admin.fetchval(
        "INSERT INTO organizacoes (nome, slug, plano_id) VALUES ($1, $2, $3) RETURNING id",
        f"S05 {sufixo}",
        f"s05-{sufixo}",
        plano_id,
    )
    usuario_id = await admin.fetchval(
        """
        INSERT INTO usuarios_operacoes (organizacao_id, nome, usuario, email, senha_hash, alterar_senha)
        VALUES ($1, 'Usuario S05', $2, $3, 'hash-teste', false) RETURNING id
        """,
        org_id,
        f"s05.{sufixo}",
        f"s05.{sufixo}@example.test",
    )
    return org_id, usuario_id


async def _estado(admin: asyncpg.Connection, usuario_id: int) -> asyncpg.Record:
    return await admin.fetchrow(
        "SELECT tentativas_falhas, bloqueado_ate FROM usuarios_operacoes WHERE id = $1", usuario_id
    )


def _fabrica():
    engine = create_async_engine(ADMIN_DSN.replace("postgresql://", "postgresql+asyncpg://", 1))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _falhar(fabrica, usuario_id: int, agora: datetime) -> bool:
    async with fabrica() as session:
        bloqueou = await registrar_falha_login(session, UsuarioOperacoes, usuario_id, agora)
        await session.commit()
        return bloqueou


async def _com_usuario(corpo) -> None:
    admin = await _conectar()
    sufixo = uuid4().hex[:12]
    org_id = None
    engine, fabrica = _fabrica()
    try:
        org_id, usuario_id = await _criar_usuario(admin, sufixo)
        await corpo(admin, fabrica, usuario_id)
    finally:
        await engine.dispose()
        if org_id is not None:
            await admin.execute("DELETE FROM usuarios_operacoes WHERE organizacao_id = $1", org_id)
            await admin.execute("DELETE FROM organizacoes WHERE id = $1", org_id)
        await admin.close()


async def test_falhas_simultaneas_abaixo_do_limite_nao_perdem_nenhuma_tentativa() -> None:
    async def corpo(admin, fabrica, usuario_id):
        agora = datetime.now(UTC)
        tarefas = (_falhar(fabrica, usuario_id, agora) for _ in range(LIMITE_FALHAS_LOGIN - 1))
        resultados = await asyncio.gather(*tarefas)
        assert resultados == [False] * (LIMITE_FALHAS_LOGIN - 1)
        estado = await _estado(admin, usuario_id)
        assert estado["tentativas_falhas"] == LIMITE_FALHAS_LOGIN - 1  # leitura+soma em Python perdia tentativas
        assert estado["bloqueado_ate"] is None

    await _com_usuario(corpo)


async def test_o_bloqueio_acontece_exatamente_uma_vez_e_depois_nada_conta() -> None:
    async def corpo(admin, fabrica, usuario_id):
        agora = datetime.now(UTC)
        resultados = await asyncio.gather(*(_falhar(fabrica, usuario_id, agora) for _ in range(20)))
        assert resultados.count(True) == 1  # só a quinta falha bloqueia
        estado = await _estado(admin, usuario_id)
        assert estado["tentativas_falhas"] == 0
        assert estado["bloqueado_ate"] is not None
        assert abs(estado["bloqueado_ate"] - (agora + BLOQUEIO_LOGIN)) < timedelta(seconds=1)

    await _com_usuario(corpo)


async def test_tentativa_com_a_conta_bloqueada_nao_conta_nem_prolonga() -> None:
    async def corpo(admin, fabrica, usuario_id):
        agora = datetime.now(UTC)
        ate = agora + timedelta(minutes=3)
        await admin.execute(
            "UPDATE usuarios_operacoes SET bloqueado_ate = $2, tentativas_falhas = 0 WHERE id = $1", usuario_id, ate
        )
        assert await _falhar(fabrica, usuario_id, agora) is False
        estado = await _estado(admin, usuario_id)
        assert estado["tentativas_falhas"] == 0
        assert estado["bloqueado_ate"] == ate  # o bloqueio em vigor não foi estendido

    await _com_usuario(corpo)


async def test_bloqueio_vencido_volta_a_contar_do_zero() -> None:
    async def corpo(admin, fabrica, usuario_id):
        agora = datetime.now(UTC)
        await admin.execute(
            "UPDATE usuarios_operacoes SET bloqueado_ate = $2, tentativas_falhas = 0 WHERE id = $1",
            usuario_id,
            agora - timedelta(minutes=1),
        )
        assert await _falhar(fabrica, usuario_id, agora) is False
        assert (await _estado(admin, usuario_id))["tentativas_falhas"] == 1

    await _com_usuario(corpo)

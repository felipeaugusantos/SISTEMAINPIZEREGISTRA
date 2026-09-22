import asyncpg

# Chave estável e exclusiva da sincronização da Seção V — Marcas.
RPI_MARCAS_ADVISORY_LOCK = 5_290_000


async def adquirir_lock_sincronizacao(database_url: str) -> asyncpg.Connection | None:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conexao = await asyncpg.connect(dsn=dsn)
    adquirido = await conexao.fetchval(
        "SELECT pg_try_advisory_lock($1)",
        RPI_MARCAS_ADVISORY_LOCK,
    )
    if adquirido:
        return conexao
    await conexao.close()
    return None


async def liberar_lock_sincronizacao(conexao: asyncpg.Connection) -> None:
    try:
        await conexao.execute(
            "SELECT pg_advisory_unlock($1)",
            RPI_MARCAS_ADVISORY_LOCK,
        )
    finally:
        await conexao.close()


async def adquirir_lock_sincronizacao_bloqueante(database_url: str) -> asyncpg.Connection:
    """Igual a adquirir_lock_sincronizacao, mas espera o lock ficar livre em
    vez de desistir -- usado por importações manuais (BADEPI) que
    escrevem na mesma tabela titulares e precisam ficar serializadas com a
    sincronização automática da RPI (achado do Codex no PR #108: sem isso,
    duas importações concorrentes podem criar titular duplicado, já que
    NOT EXISTS sozinho só enxerga um retrato do momento, sem a constraint
    única que normalmente bloquearia a corrida)."""
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conexao = await asyncpg.connect(dsn=dsn)
    await conexao.execute("SELECT pg_advisory_lock($1)", RPI_MARCAS_ADVISORY_LOCK)
    return conexao

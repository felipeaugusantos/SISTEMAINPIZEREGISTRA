import os
from datetime import date
from uuid import uuid4

import asyncpg
import pytest

from app.models import TipoProcesso
from app.rpi.bulk_importer import _criar_tabelas_temporarias, _importar_lote
from app.rpi.types import RegistroRpi, TitularRpi

ADMIN_DSN = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")


async def _conectar() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(ADMIN_DSN)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL real indisponível: {type(exc).__name__}")


async def test_importar_titulares_nao_falha_sem_a_constraint_unica_e_evita_duplicata() -> None:
    """Achado 22/09/2026: a sincronização automática da RPI estava travada em
    toda execução -- uq_titulares_nome_pais não existe em produção desde o
    incidente de restore de 16/09/2026 (reconciliação dos 13.836 grupos
    duplicados ainda pendente de revisão humana), e o INSERT usava
    ON CONFLICT (nome, pais), que o Postgres rejeita com
    InvalidColumnReferenceError quando a constraint referenciada não existe.
    Este teste não cria a constraint (espelhando o estado real de produção) e
    confirma que a importação segue funcionando sem duplicar titular já
    existente."""
    conexao = await _conectar()
    transacao = conexao.transaction()
    await transacao.start()
    try:
        sufixo = uuid4().hex[:10]
        nome_existente = f"Titular Existente {sufixo}"
        nome_novo = f"Titular Novo {sufixo}"
        await conexao.execute("INSERT INTO titulares (nome, pais) VALUES ($1, 'BR')", nome_existente)

        await _criar_tabelas_temporarias(conexao)
        registro = RegistroRpi(
            numero=f"92{sufixo[:6]}",
            tipo=TipoProcesso.MARCA,
            titulo="Marca Teste",
            data_deposito=date(2026, 1, 1),
            situacao=None,
            numero_rpi=2907,
            data_rpi=date(2026, 9, 22),
            fonte_arquivo="rpi2907.zip",
            titulares=(
                TitularRpi(nome=nome_existente, pais="BR"),
                TitularRpi(nome=nome_novo, pais="BR"),
            ),
            movimentacoes=(),
        )

        await _importar_lote(conexao, [registro])

        total_existente = await conexao.fetchval(
            "SELECT count(*) FROM titulares WHERE nome = $1 AND pais = 'BR'", nome_existente
        )
        assert total_existente == 1

        novo_id = await conexao.fetchval(
            "SELECT id FROM titulares WHERE nome = $1 AND pais = 'BR'", nome_novo
        )
        assert novo_id is not None
    finally:
        await transacao.rollback()
        await conexao.close()

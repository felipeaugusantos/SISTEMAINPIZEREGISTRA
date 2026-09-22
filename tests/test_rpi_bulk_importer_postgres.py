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
        # A migration c82fd71a40e3 recria uq_titulares_nome_pais no banco de
        # teste do CI -- sem derrubá-la aqui, o teste passaria mesmo com o
        # ON CONFLICT antigo e não pegaria a regressão real de produção
        # (achado do Codex no PR #108). O DROP é transacional, então some
        # com o rollback do finally, sem afetar outros testes.
        await conexao.execute("ALTER TABLE titulares DROP CONSTRAINT IF EXISTS uq_titulares_nome_pais")

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


async def test_importar_vincula_processo_a_um_unico_titular_mesmo_com_grupo_duplicado() -> None:
    """Achado do Codex (PR #108): com titular duplicado (os 13.836 grupos
    reais que ainda existem em produção), um JOIN direto por (nome, pais)
    batia em todas as duplicatas e ligava o processo a cada uma delas. Este
    teste cria um grupo duplicado de propósito e confirma que só um vínculo
    (o do id canônico) é criado."""
    conexao = await _conectar()
    transacao = conexao.transaction()
    await transacao.start()
    try:
        await conexao.execute("ALTER TABLE titulares DROP CONSTRAINT IF EXISTS uq_titulares_nome_pais")

        sufixo = uuid4().hex[:10]
        nome_duplicado = f"Titular Duplicado {sufixo}"
        id_canonico = await conexao.fetchval(
            "INSERT INTO titulares (nome, pais) VALUES ($1, 'BR') RETURNING id", nome_duplicado
        )
        await conexao.execute("INSERT INTO titulares (nome, pais) VALUES ($1, 'BR')", nome_duplicado)

        await _criar_tabelas_temporarias(conexao)
        registro = RegistroRpi(
            numero=f"93{sufixo[:6]}",
            tipo=TipoProcesso.MARCA,
            titulo="Marca Teste",
            data_deposito=date(2026, 1, 1),
            situacao=None,
            numero_rpi=2907,
            data_rpi=date(2026, 9, 22),
            fonte_arquivo="rpi2907.zip",
            titulares=(TitularRpi(nome=nome_duplicado, pais="BR"),),
            movimentacoes=(),
        )

        await _importar_lote(conexao, [registro])

        vinculos = await conexao.fetch(
            """
            SELECT pt.titular_id FROM processo_titulares AS pt
            JOIN processos AS p ON p.id = pt.processo_id
            WHERE p.numero = $1
            """,
            registro.numero,
        )
        assert [linha["titular_id"] for linha in vinculos] == [id_canonico]
    finally:
        await transacao.rollback()
        await conexao.close()

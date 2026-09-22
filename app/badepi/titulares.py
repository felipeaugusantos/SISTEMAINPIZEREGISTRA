import csv
from collections.abc import Callable, Iterator
from pathlib import Path

import asyncpg

from app.rpi.locking import adquirir_lock_sincronizacao_bloqueante, liberar_lock_sincronizacao


def ler_titulares_marcas(
    arquivo: Path,
    limite: int | None = None,
) -> Iterator[tuple[str, str, str | None]]:
    """Lê número do pedido, nome e país dos depositantes do BADEPI."""
    with arquivo.open(encoding="cp1252", newline="") as entrada:
        leitor = csv.DictReader(entrada, delimiter=";")
        campos = set(leitor.fieldnames or [])
        obrigatorios = {"NO_PEDIDO", "NM_COMPLET_PFPJ", "CD_PAIS_PFPJ"}
        ausentes = obrigatorios - campos
        if ausentes:
            raise ValueError(f"Colunas BADEPI ausentes: {', '.join(sorted(ausentes))}")

        processados = 0
        for linha in leitor:
            if limite is not None and processados >= limite:
                break

            numero = (linha["NO_PEDIDO"] or "").strip()
            nome = (linha["NM_COMPLET_PFPJ"] or "").strip()
            if not numero or not nome:
                continue

            pais = (linha["CD_PAIS_PFPJ"] or "").strip().upper() or None
            yield numero, nome, pais
            processados += 1


async def importar_titulares_marcas(
    database_url: str,
    arquivo: Path,
    limite: int | None = None,
    tamanho_lote: int = 50_000,
    progresso: Callable[[int], None] | None = None,
) -> int:
    """Cria titulares únicos e os associa aos processos de marca em lotes."""
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    # Serializa com a sincronização automática da RPI (mesmo lock) -- as duas
    # escrevem em titulares por NOT EXISTS, que sem a constraint única (achado
    # do Codex no PR #108) não impede duplicata entre importações concorrentes.
    lock = await adquirir_lock_sincronizacao_bloqueante(database_url)
    conexao = await asyncpg.connect(dsn=dsn)
    processados = 0

    try:
        await conexao.execute(
            """
            CREATE TEMP TABLE badepi_titulares_lote (
                numero text NOT NULL,
                nome text NOT NULL,
                pais varchar(2)
            ) ON COMMIT PRESERVE ROWS
            """
        )

        lote: list[tuple[str, str, str | None]] = []
        for registro in ler_titulares_marcas(arquivo, limite):
            lote.append(registro)
            if len(lote) < tamanho_lote:
                continue

            await _importar_lote(conexao, lote)
            processados += len(lote)
            lote.clear()
            if progresso:
                progresso(processados)

        if lote:
            await _importar_lote(conexao, lote)
            processados += len(lote)
            if progresso:
                progresso(processados)
    finally:
        await conexao.close()
        await liberar_lock_sincronizacao(lock)

    return processados


async def _importar_lote(
    conexao: asyncpg.Connection,
    lote: list[tuple[str, str, str | None]],
) -> None:
    async with conexao.transaction():
        await conexao.copy_records_to_table(
            "badepi_titulares_lote",
            records=lote,
            columns=("numero", "nome", "pais"),
        )
        # Mesmo achado do app/rpi/bulk_importer.py: a constraint
        # uq_titulares_nome_pais não existe hoje em produção (índice
        # corrompido derrubado num incidente de restore em 16/09/2026), então
        # "ON CONFLICT (nome, pais)" seria rejeitado com
        # InvalidColumnReferenceError. NOT EXISTS não depende dela.
        await conexao.execute(
            """
            INSERT INTO titulares (nome, pais)
            SELECT DISTINCT origem.nome, origem.pais
            FROM badepi_titulares_lote AS origem
            WHERE NOT EXISTS (
                SELECT 1 FROM titulares AS existente
                WHERE existente.nome = origem.nome
                  AND existente.pais IS NOT DISTINCT FROM origem.pais
            )
            """
        )
        await conexao.execute(
            """
            INSERT INTO processo_titulares (processo_id, titular_id)
            SELECT DISTINCT processo.id, titular.id
            FROM badepi_titulares_lote AS origem
            JOIN processos AS processo
              ON processo.numero_normalizado = upper(
                  regexp_replace(origem.numero, '[^A-Za-z0-9]', '', 'g')
              )
            JOIN titulares AS titular
              ON titular.nome = origem.nome
             AND titular.pais IS NOT DISTINCT FROM origem.pais
            ON CONFLICT DO NOTHING
            """
        )
        await conexao.execute("TRUNCATE badepi_titulares_lote")

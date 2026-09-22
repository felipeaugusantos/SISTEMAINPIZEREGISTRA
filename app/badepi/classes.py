import csv
from collections.abc import Callable, Iterator
from pathlib import Path

import asyncpg

from app.rpi.locking import adquirir_lock_sincronizacao_bloqueante, liberar_lock_sincronizacao


def ler_classes_marcas(
    arquivo: Path,
    limite: int | None = None,
) -> Iterator[tuple[str, str, str | None, str | None]]:
    """Lê as classes Nice do arquivo histórico de marcas do BADEPI."""
    with arquivo.open(encoding="cp1252", newline="") as entrada:
        leitor = csv.DictReader(entrada, delimiter=";")
        obrigatorios = {"NO_PEDIDO", "CD_CLASSE_NICE", "REV_CLASSE_NICE", "ST_CLASSE_NICE"}
        ausentes = obrigatorios - set(leitor.fieldnames or [])
        if ausentes:
            raise ValueError(f"Colunas BADEPI ausentes: {', '.join(sorted(ausentes))}")

        for indice, linha in enumerate(leitor):
            if limite is not None and indice >= limite:
                break
            numero = (linha["NO_PEDIDO"] or "").strip()
            codigo = (linha["CD_CLASSE_NICE"] or "").strip()
            if not numero or not codigo or not codigo.isdigit():
                continue
            edicao = (linha["REV_CLASSE_NICE"] or "").strip() or None
            status = (linha["ST_CLASSE_NICE"] or "").strip() or None
            yield numero, str(int(codigo)), edicao, status


async def importar_classes_marcas(
    database_url: str,
    arquivo: Path,
    limite: int | None = None,
    tamanho_lote: int = 100_000,
    progresso: Callable[[int], None] | None = None,
) -> int:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    # Achado do Codex (PR #109): sem a constraint única, o UPDATE+INSERT
    # dentro da mesma WITH só coordena as CTEs de um único statement, não
    # serializa sessões concorrentes -- mesmo lock que app/badepi/titulares.py
    # já usa pra ficar serializado com a sincronização automática da RPI.
    lock = await adquirir_lock_sincronizacao_bloqueante(database_url)
    conexao = await asyncpg.connect(dsn=dsn)
    processados = 0
    try:
        await conexao.execute(
            """
            CREATE TEMP TABLE badepi_classes_lote (
                numero text NOT NULL,
                codigo text NOT NULL,
                edicao text,
                status text
            ) ON COMMIT PRESERVE ROWS
            """
        )
        lote: list[tuple[str, str, str | None, str | None]] = []
        for registro in ler_classes_marcas(arquivo, limite):
            lote.append(registro)
            if len(lote) < tamanho_lote:
                continue
            processados += await _importar_lote(conexao, lote)
            lote.clear()
            if progresso:
                progresso(processados)
        if lote:
            processados += await _importar_lote(conexao, lote)
            if progresso:
                progresso(processados)
    finally:
        await conexao.close()
        await liberar_lock_sincronizacao(lock)
    return processados


async def _importar_lote(
    conexao: asyncpg.Connection,
    lote: list[tuple[str, str, str | None, str | None]],
) -> int:
    async with conexao.transaction():
        await conexao.copy_records_to_table(
            "badepi_classes_lote",
            records=lote,
            columns=("numero", "codigo", "edicao", "status"),
        )
        # Mesmo achado do app/rpi/bulk_importer.py: a constraint
        # uq_classificacoes_marca_processo_sistema_codigo não existe hoje em
        # produção (mesmo incidente de restore de 16/09/2026). O ON CONFLICT
        # DO UPDATE vira um UPDATE+INSERT explícito na mesma WITH (mesmo
        # snapshot, sem corrida entre as duas etapas); o SELECT final soma as
        # duas contagens pra manter o retorno de "quantidade processada".
        total = await conexao.fetchval(
            """
            WITH origem_dedup AS (
                SELECT DISTINCT ON (p.id, c.codigo)
                    p.id AS processo_id, 'nice' AS sistema, c.codigo,
                    nullif(c.edicao, '0') AS edicao, c.status
                FROM badepi_classes_lote c
                JOIN processos p ON p.numero_normalizado =
                    upper(regexp_replace(c.numero, '[^A-Za-z0-9]', '', 'g'))
                WHERE p.tipo = 'marca'
                ORDER BY p.id, c.codigo
            ),
            atualizadas AS (
                UPDATE classificacoes_marca AS existente
                SET edicao = coalesce(existente.edicao, od.edicao),
                    status = coalesce(existente.status, od.status)
                FROM origem_dedup AS od
                WHERE existente.processo_id = od.processo_id
                  AND existente.sistema = od.sistema
                  AND existente.codigo = od.codigo
                RETURNING existente.processo_id, existente.sistema, existente.codigo
            ),
            inseridas AS (
                INSERT INTO classificacoes_marca (processo_id, sistema, codigo, edicao, status)
                SELECT od.processo_id, od.sistema, od.codigo, od.edicao, od.status
                FROM origem_dedup AS od
                WHERE NOT EXISTS (
                    SELECT 1 FROM atualizadas AS a
                    WHERE a.processo_id = od.processo_id
                      AND a.sistema = od.sistema
                      AND a.codigo = od.codigo
                )
                RETURNING processo_id
            )
            SELECT (SELECT count(*) FROM atualizadas) + (SELECT count(*) FROM inseridas)
            """
        )
        await conexao.execute("TRUNCATE badepi_classes_lote")
    return total or 0

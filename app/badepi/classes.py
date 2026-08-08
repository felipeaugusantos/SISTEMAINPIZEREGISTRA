import csv
from collections.abc import Callable, Iterator
from pathlib import Path

import asyncpg


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


def _quantidade(resultado: str) -> int:
    try:
        return int(resultado.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def importar_classes_marcas(
    database_url: str,
    arquivo: Path,
    limite: int | None = None,
    tamanho_lote: int = 100_000,
    progresso: Callable[[int], None] | None = None,
) -> int:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
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
        resultado = await conexao.execute(
            """
            INSERT INTO classificacoes_marca (processo_id, sistema, codigo, edicao, status)
            SELECT DISTINCT ON (p.id, c.codigo)
                p.id, 'nice', c.codigo, nullif(c.edicao, '0'), c.status
            FROM badepi_classes_lote c
            JOIN processos p ON p.numero_normalizado =
                upper(regexp_replace(c.numero, '[^A-Za-z0-9]', '', 'g'))
            WHERE p.tipo = 'marca'
            ORDER BY p.id, c.codigo
            ON CONFLICT (processo_id, sistema, codigo) DO UPDATE SET
                edicao = coalesce(classificacoes_marca.edicao, excluded.edicao),
                status = coalesce(classificacoes_marca.status, excluded.status)
            """
        )
        await conexao.execute("TRUNCATE badepi_classes_lote")
    return _quantidade(resultado)

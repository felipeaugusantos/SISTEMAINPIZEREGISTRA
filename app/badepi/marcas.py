import csv
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import asyncpg

FONTE_BADEPI = "BADEPI v11 (2000-2024)"


def ler_depositos_marcas(
    arquivo: Path,
    limite: int | None = None,
) -> Iterator[tuple[str, str | None, date | None]]:
    """Lê o CSV de depósitos de marcas distribuído pelo INPI."""
    with arquivo.open(encoding="cp1252", newline="") as entrada:
        leitor = csv.DictReader(entrada, delimiter=";")
        campos = set(leitor.fieldnames or [])
        obrigatorios = {"NO_PEDIDO", "NM_TITULO_MARCA", "DT_DEPOSITO"}
        ausentes = obrigatorios - campos
        if ausentes:
            raise ValueError(f"Colunas BADEPI ausentes: {', '.join(sorted(ausentes))}")

        for indice, linha in enumerate(leitor):
            if limite is not None and indice >= limite:
                break

            numero = (linha["NO_PEDIDO"] or "").strip()
            if not numero:
                continue

            titulo = (linha["NM_TITULO_MARCA"] or "").strip() or None
            data_texto = (linha["DT_DEPOSITO"] or "").split(" ", 1)[0]
            data_deposito = None
            if data_texto:
                dia, mes, ano = data_texto.split("/")
                data_deposito = date(int(ano), int(mes), int(dia))

            yield numero, titulo, data_deposito


def _quantidade_comando(resultado: str) -> int:
    try:
        return int(resultado.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def importar_depositos_marcas(
    database_url: str,
    arquivo: Path,
    limite: int | None = None,
    tamanho_lote: int = 50_000,
    progresso: Callable[[int], None] | None = None,
) -> int:
    """Importa depósitos BADEPI em lotes, com retomada segura por número do pedido."""
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conexao = await asyncpg.connect(dsn=dsn)
    processados = 0

    try:
        await conexao.execute(
            """
            CREATE TEMP TABLE badepi_marcas_lote (
                numero text NOT NULL,
                titulo text,
                data_deposito date
            ) ON COMMIT PRESERVE ROWS
            """
        )

        lote: list[tuple[str, str | None, date | None]] = []
        for registro in ler_depositos_marcas(arquivo, limite):
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
    lote: list[tuple[str, str | None, date | None]],
) -> int:
    async with conexao.transaction():
        await conexao.copy_records_to_table(
            "badepi_marcas_lote",
            records=lote,
            columns=("numero", "titulo", "data_deposito"),
        )
        resultado = await conexao.execute(
            """
            INSERT INTO processos (
                numero,
                numero_normalizado,
                tipo,
                titulo,
                data_deposito,
                situacao,
                fonte
            )
            SELECT DISTINCT ON (numero_normalizado)
                numero,
                numero_normalizado,
                'marca',
                titulo,
                data_deposito,
                NULL,
                $1
            FROM (
                SELECT
                    numero,
                    titulo,
                    data_deposito,
                    upper(regexp_replace(numero, '[^A-Za-z0-9]', '', 'g'))
                        AS numero_normalizado
                FROM badepi_marcas_lote
            ) AS origem
            ORDER BY numero_normalizado
            ON CONFLICT (numero_normalizado) DO UPDATE SET
                titulo = coalesce(processos.titulo, excluded.titulo),
                data_deposito = coalesce(processos.data_deposito, excluded.data_deposito),
                fonte = CASE
                    WHEN processos.fonte LIKE 'RPI %' THEN processos.fonte
                    ELSE excluded.fonte
                END
            """,
            FONTE_BADEPI,
        )
        await conexao.execute("TRUNCATE badepi_marcas_lote")
    return _quantidade_comando(resultado)

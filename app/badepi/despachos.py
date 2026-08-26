import csv
from collections.abc import Callable, Iterator
from datetime import date
from hashlib import sha256
from pathlib import Path

import asyncpg

from app.badepi.despachos_codigos import codigo_numerico, descricao_despacho
from app.normalization import normalizar_numero_processo

FONTE_BADEPI_DESPACHOS = "BADEPI v11 (2000-2024) despachos"


def _descricao(codigo: str) -> str:
    """Descrição oficial do despacho; usa o próprio código como rótulo se não houver mapa."""
    return descricao_despacho(codigo) or f"Despacho {codigo}"


def _chave_origem(numero: str, numero_rpi: int, codigo: str) -> str:
    # Dedupe pelo código NUMÉRICO (não o bruto): o BADEPI grava o mesmo despacho
    # sob duas formas (ex.: "009" e "IPAS009"), que devem colapsar em uma única
    # movimentação. A descrição fica fora da chave para não reabrir a duplicata
    # via fallback de códigos legados ("Despacho 150" vs "Despacho DESP150").
    conteudo = "|".join(
        (
            "marca",
            str(numero_rpi),
            normalizar_numero_processo(numero),
            codigo_numerico(codigo) or codigo,
        )
    )
    return sha256(conteudo.encode()).hexdigest()


def ler_despachos_marcas(
    arquivo: Path,
    limite: int | None = None,
) -> Iterator[tuple[str, str, str, date, int, str]]:
    """Lê o CSV de despachos de marcas do BADEPI.

    Emite ``(numero, codigo, descricao, data_rpi, numero_rpi, chave_origem)``.
    Linhas sem número, sem RPI ou sem data são descartadas — ``movimentacoes``
    exige ``data_rpi`` e ``numero_rpi`` não nulos.
    """
    with arquivo.open(encoding="cp1252", newline="") as entrada:
        leitor = csv.DictReader(entrada, delimiter=";")
        campos = set(leitor.fieldnames or [])
        obrigatorios = {"NO_PEDIDO", "NO_RPI", "DT_PUBLICACAO", "CD_DESPACH_RPI"}
        ausentes = obrigatorios - campos
        if ausentes:
            raise ValueError(f"Colunas BADEPI ausentes: {', '.join(sorted(ausentes))}")

        processados = 0
        for linha in leitor:
            if limite is not None and processados >= limite:
                break

            numero = (linha["NO_PEDIDO"] or "").strip()
            codigo = (linha["CD_DESPACH_RPI"] or "").strip()
            if not numero or not codigo:
                continue

            try:
                numero_rpi = int((linha["NO_RPI"] or "").strip())
            except ValueError:
                continue

            data_texto = (linha["DT_PUBLICACAO"] or "").split(" ", 1)[0]
            if not data_texto:
                continue
            try:
                dia, mes, ano = data_texto.split("/")
                data_rpi = date(int(ano), int(mes), int(dia))
            except ValueError:
                continue

            descricao = _descricao(codigo)
            chave = _chave_origem(numero, numero_rpi, codigo)
            yield numero, codigo, descricao, data_rpi, numero_rpi, chave
            processados += 1


async def importar_despachos_marcas(
    database_url: str,
    arquivo: Path,
    limite: int | None = None,
    tamanho_lote: int = 50_000,
    progresso: Callable[[int], None] | None = None,
) -> int:
    """Importa despachos BADEPI como movimentações, associando por número do pedido."""
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conexao = await asyncpg.connect(dsn=dsn)
    processados = 0

    try:
        await conexao.execute(
            """
            CREATE TEMP TABLE badepi_despachos_lote (
                numero text NOT NULL,
                codigo text NOT NULL,
                descricao text NOT NULL,
                data_rpi date NOT NULL,
                numero_rpi integer NOT NULL,
                chave_origem varchar(64) NOT NULL
            ) ON COMMIT PRESERVE ROWS
            """
        )

        lote: list[tuple[str, str, str, date, int, str]] = []
        for registro in ler_despachos_marcas(arquivo, limite):
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

    return processados


async def _importar_lote(
    conexao: asyncpg.Connection,
    lote: list[tuple[str, str, str, date, int, str]],
) -> None:
    async with conexao.transaction():
        await conexao.copy_records_to_table(
            "badepi_despachos_lote",
            records=lote,
            columns=("numero", "codigo", "descricao", "data_rpi", "numero_rpi", "chave_origem"),
        )
        await conexao.execute(
            """
            INSERT INTO movimentacoes (
                processo_id, codigo_despacho, descricao,
                data_rpi, numero_rpi, fonte_arquivo, chave_origem
            )
            SELECT DISTINCT ON (origem.chave_origem)
                processo.id,
                origem.codigo,
                origem.descricao,
                origem.data_rpi,
                origem.numero_rpi,
                $1,
                origem.chave_origem
            FROM badepi_despachos_lote AS origem
            JOIN processos AS processo
              ON processo.numero_normalizado = upper(
                  regexp_replace(origem.numero, '[^A-Za-z0-9]', '', 'g')
              )
             AND processo.tipo = 'marca'
            ORDER BY origem.chave_origem, (origem.codigo LIKE 'IPAS%') DESC, origem.codigo
            ON CONFLICT (chave_origem) DO NOTHING
            """,
            FONTE_BADEPI_DESPACHOS,
        )
        await conexao.execute("TRUNCATE badepi_despachos_lote")

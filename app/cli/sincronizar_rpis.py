import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

from app.models import TipoProcesso
from app.rpi.bulk_importer import importar_rpi_em_lotes
from app.rpi.parsers import ler_marcas, ler_patentes
from app.rpi.sync import baixar_e_extrair_rpi
from app.settings import get_settings


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Baixa e importa uma sequência de RPIs")
    parser.add_argument("--inicio", required=True, type=int)
    parser.add_argument("--fim", required=True, type=int)
    parser.add_argument(
        "--tipo",
        choices=("marca", "patente", "ambos"),
        default="ambos",
    )
    parser.add_argument("--diretorio", type=Path, default=Path("data/raw/rpis"))
    parser.add_argument("--forcar", action="store_true")
    return parser.parse_args()


async def _ja_importada(database_url: str, numero: int, tipo: TipoProcesso) -> bool:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conexao = await asyncpg.connect(dsn=dsn)
    try:
        return bool(
            await conexao.fetchval(
                "SELECT EXISTS(SELECT 1 FROM rpi_importacoes WHERE numero_rpi=$1 AND tipo=$2)",
                numero,
                tipo.value,
            )
        )
    finally:
        await conexao.close()


async def _registrar_importacao(
    database_url: str,
    numero: int,
    tipo: TipoProcesso,
    registros: int,
) -> None:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conexao = await asyncpg.connect(dsn=dsn)
    try:
        await conexao.execute(
            """
            INSERT INTO rpi_importacoes (numero_rpi, tipo, registros_processados)
            VALUES ($1, $2, $3)
            ON CONFLICT (numero_rpi, tipo) DO UPDATE SET
                registros_processados=excluded.registros_processados,
                importado_em=now()
            """,
            numero,
            tipo.value,
            registros,
        )
    finally:
        await conexao.close()


async def executar() -> None:
    args = argumentos()
    if args.inicio > args.fim:
        raise SystemExit("--inicio deve ser menor ou igual a --fim")

    tipos = (
        (TipoProcesso.MARCA, TipoProcesso.PATENTE)
        if args.tipo == "ambos"
        else (TipoProcesso(args.tipo),)
    )
    database_url = get_settings().database_url

    for numero in range(args.inicio, args.fim + 1):
        for tipo in tipos:
            if not args.forcar and await _ja_importada(database_url, numero, tipo):
                print(f"RPI {numero} ({tipo.value}) já importada", file=sys.stderr)
                continue

            xml = baixar_e_extrair_rpi(numero, tipo, args.diretorio, print)
            leitor = ler_marcas if tipo is TipoProcesso.MARCA else ler_patentes
            total = await importar_rpi_em_lotes(database_url, leitor(xml))
            await _registrar_importacao(database_url, numero, tipo, total)
            print(f"RPI {numero} ({tipo.value}): {total:,} registros", flush=True)


if __name__ == "__main__":
    asyncio.run(executar())

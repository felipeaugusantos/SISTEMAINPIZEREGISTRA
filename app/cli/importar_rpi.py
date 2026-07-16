import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from app.database import session_factory
from app.models import TipoProcesso
from app.rpi.importer import importar_registros
from app.rpi.parsers import ler_marcas, ler_patentes


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa uma edição XML da RPI")
    parser.add_argument("--arquivo", required=True, type=Path)
    parser.add_argument("--tipo", required=True, choices=[tipo.value for tipo in TipoProcesso])
    parser.add_argument("--limite", type=int)
    return parser.parse_args()


async def executar() -> None:
    args = argumentos()
    tipo = TipoProcesso(args.tipo)
    leitor = ler_marcas if tipo is TipoProcesso.MARCA else ler_patentes

    async with session_factory() as session:
        resultado = await importar_registros(session, leitor(args.arquivo), args.limite)
        await session.commit()

    print(json.dumps(asdict(resultado), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(executar())

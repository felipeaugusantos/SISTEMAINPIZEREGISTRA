import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.badepi.despachos import importar_despachos_marcas
from app.settings import get_settings


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa despachos de marcas do BADEPI")
    parser.add_argument("--arquivo", required=True, type=Path)
    parser.add_argument("--limite", type=int)
    parser.add_argument("--lote", type=int, default=50_000)
    return parser.parse_args()


async def executar() -> None:
    args = argumentos()
    if not args.arquivo.is_file():
        raise SystemExit(f"Arquivo não encontrado: {args.arquivo}")

    def exibir_progresso(processados: int) -> None:
        print(f"Importados: {processados:,}", file=sys.stderr, flush=True)

    total = await importar_despachos_marcas(
        database_url=get_settings().database_url,
        arquivo=args.arquivo,
        limite=args.limite,
        tamanho_lote=args.lote,
        progresso=exibir_progresso,
    )
    print(json.dumps({"registros_processados": total}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(executar())

import argparse
import asyncio

from app.database import session_factory
from app.trademarks.learning import (
    ativar_modelo,
    construir_dataset_historico,
    treinar_modelo,
)


async def executar(args: argparse.Namespace) -> None:
    async with session_factory() as session:
        if args.dataset:
            rotulos, pares = await construir_dataset_historico(
                session,
                limite=args.limite,
                candidatos_por_processo=args.candidatos,
            )
            print(f"Dataset: {rotulos} rótulos e {pares} pares processados")
        if args.treinar:
            modelo = await treinar_modelo(session)
            print(f"Modelo {modelo.status}: {modelo.versao} · métricas {modelo.metricas}")
            if args.ativar:
                await ativar_modelo(session, modelo, args.administrador)
                print("Modelo promovido explicitamente de VALIDATION para ACTIVE")


def main() -> None:
    parser = argparse.ArgumentParser(description="Constrói o dataset temporal e treina o modelo de registrabilidade.")
    parser.add_argument("--dataset", action="store_true")
    parser.add_argument("--treinar", action="store_true")
    parser.add_argument("--ativar", action="store_true")
    parser.add_argument("--limite", type=int, default=3000)
    parser.add_argument("--candidatos", type=int, default=12)
    parser.add_argument("--administrador", default="cli")
    args = parser.parse_args()
    if not args.dataset and not args.treinar:
        parser.error("Informe --dataset e/ou --treinar")
    asyncio.run(executar(args))


if __name__ == "__main__":
    main()

import argparse
import asyncio

from app.database import session_factory
from app.tenancy import aplicar_contexto_tenant
from app.trademarks.agent import reprocessar_agentes_pendentes


async def executar(organizacao_id: int) -> None:
    async with session_factory() as session:
        await aplicar_contexto_tenant(session, organizacao_id, superadmin=True)
        resultado = await reprocessar_agentes_pendentes(
            session,
            organizacao_id=organizacao_id,
        )
        await session.commit()
    print(
        "Reprocessamento concluído: "
        f"{resultado['processadas']} de {resultado['pendentes_encontradas']} pesquisa(s)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cria snapshots do Agente de Registrabilidade ainda pendentes."
    )
    parser.add_argument("--organizacao-id", type=int, default=1)
    argumentos = parser.parse_args()
    asyncio.run(executar(argumentos.organizacao_id))


if __name__ == "__main__":
    main()

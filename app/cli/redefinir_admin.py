"""Redefine a senha de um usuário administrativo (recuperação de acesso).

A nova senha vem da variável de ambiente NOVA_SENHA — nunca de argumento em linha
de comando — para não vazar no histórico do shell nem em logs de processo.

Uso (PowerShell):
    docker exec -e NOVA_SENHA=suasenha inpi-api-1 \
        /app/.venv/bin/python -m app.cli.redefinir_admin

Opcional: ADMIN_USUARIO (padrão "admin") para escolher qual usuário redefinir.
"""

import asyncio
import os

from sqlalchemy import text

from app.auth import hash_senha
from app.database import session_factory


async def executar() -> None:
    nova = os.environ.get("NOVA_SENHA")
    usuario = os.environ.get("ADMIN_USUARIO", "admin")
    if not nova or len(nova) < 8:
        raise SystemExit("Defina NOVA_SENHA (mínimo 8 caracteres) no ambiente.")
    async with session_factory() as session:
        # Contexto de superadmin para satisfazer o RLS de usuarios_operacoes.
        await session.execute(text("SET app.superadmin = 'true'"))
        resultado = await session.execute(
            text(
                "UPDATE usuarios_operacoes SET senha_hash = :h, tentativas_falhas = 0, "
                "bloqueado_ate = NULL, alterar_senha = false WHERE usuario = :u"
            ),
            {"h": hash_senha(nova), "u": usuario},
        )
        await session.commit()
        if resultado.rowcount:
            print(f"Senha redefinida e conta desbloqueada (usuario={usuario}).")
        else:
            print(f"Nenhum usuário '{usuario}' encontrado.")


def main() -> None:
    asyncio.run(executar())


if __name__ == "__main__":
    main()

"""Redefine a senha de um usuário administrativo (recuperação de acesso).

A nova senha é solicitada sem eco no terminal. NOVA_SENHA também é aceita
para execução controlada, sem colocar o valor na linha de comando.

Uso (PowerShell):
    docker exec -it inpi-api-1 \
        /app/.venv/bin/python -m app.cli.redefinir_admin

Opcional: ADMIN_USUARIO (padrão "admin") para escolher qual usuário redefinir.
"""

import asyncio
import os
from getpass import getpass

from sqlalchemy import text

from app.auth import hash_senha
from app.database import session_factory
from app.password_recovery import invalidar_links_recuperacao
from app.security_ext import validar_forca_senha
from app.settings import TAMANHO_MINIMO_SENHA


async def executar() -> None:
    nova = os.environ.get("NOVA_SENHA") or getpass("Nova senha: ")
    usuario = os.environ.get("ADMIN_USUARIO", "admin")
    if len(nova) < TAMANHO_MINIMO_SENHA:
        raise SystemExit(f"A senha deve ter no mínimo {TAMANHO_MINIMO_SENHA} caracteres.")
    try:
        validar_forca_senha(nova)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    async with session_factory() as session:
        # Contexto de superadmin para satisfazer o RLS de usuarios_operacoes.
        await session.execute(text("SET LOCAL app.superadmin = 'true'"))
        resultado = await session.execute(
            text(
                "UPDATE usuarios_operacoes SET senha_hash = :h, tentativas_falhas = 0, "
                "bloqueado_ate = NULL, alterar_senha = false, senha_temporaria_expira_em = NULL, "
                "senha_versao = senha_versao + 1 WHERE usuario = :u AND ativo = true RETURNING id"
            ),
            {"h": hash_senha(nova), "u": usuario},
        )
        usuario_id = resultado.scalar_one_or_none()
        if usuario_id is not None:
            await invalidar_links_recuperacao(session, usuario_id)
            await session.execute(
                text(
                    "UPDATE sessoes_operacoes SET revogada_em = now(), motivo_revogacao = 'recuperacao_servidor' "
                    "WHERE usuario_id = :id AND revogada_em IS NULL"
                ),
                {"id": usuario_id},
            )
            await session.execute(
                text(
                    "INSERT INTO eventos_auditoria (organizacao_id, ator, acao, recurso, sucesso, status_http, detalhes) "
                    "SELECT organizacao_id, 'recuperacao_servidor', 'REDEFINIR_SENHA', :recurso, true, 200, '{}' "
                    "FROM usuarios_operacoes WHERE id = :id"
                ),
                {"id": usuario_id, "recurso": f"usuario:{usuario_id}"},
            )
            await session.commit()
            print(f"Senha redefinida e conta desbloqueada (usuario={usuario}).")
        else:
            raise SystemExit(f"Nenhum usuário ativo '{usuario}' encontrado.")


def main() -> None:
    asyncio.run(executar())


if __name__ == "__main__":
    main()

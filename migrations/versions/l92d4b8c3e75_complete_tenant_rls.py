"""complete tenant isolation for access-control tables

Revision ID: l92d4b8c3e75
Revises: k81c3a7f2b64
"""

from collections.abc import Sequence

from alembic import op

revision: str = "l92d4b8c3e75"
down_revision: str | None = "k81c3a7f2b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"
BOOTSTRAP = "NULLIF(current_setting('app.organizacao_id', true), '') IS NULL"


def _direta(tabela: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_bootstrap_read ON "{tabela}" FOR SELECT '
        f'USING ({BOOTSTRAP} OR {SUPER} OR organizacao_id = {TENANT})'
    )
    op.execute(
        f'CREATE POLICY tenant_write ON "{tabela}" FOR ALL '
        f'USING ({SUPER} OR organizacao_id = {TENANT}) '
        f'WITH CHECK ({SUPER} OR organizacao_id = {TENANT})'
    )


def _derivada_usuario(tabela: str, usuario_coluna: str = "usuario_id") -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    acesso = (
        f"EXISTS (SELECT 1 FROM usuarios_operacoes u WHERE u.id = {usuario_coluna} "
        f"AND ({SUPER} OR u.organizacao_id = {TENANT}))"
    )
    op.execute(
        f'CREATE POLICY tenant_bootstrap_read ON "{tabela}" FOR SELECT '
        f'USING ({BOOTSTRAP} OR {SUPER} OR {acesso})'
    )
    op.execute(
        f'CREATE POLICY tenant_write ON "{tabela}" FOR ALL '
        f'USING ({SUPER} OR {acesso}) WITH CHECK ({SUPER} OR {acesso})'
    )


def upgrade() -> None:
    for tabela in (
        "dominios_organizacao",
        "credenciais_integracao",
        "convites_organizacao",
        "usuarios_operacoes",
    ):
        _direta(tabela)
    _derivada_usuario("sessoes_operacoes")
    _derivada_usuario("tokens_recuperacao_senha")
    _derivada_usuario("usuario_permissoes")


def downgrade() -> None:
    for tabela in (
        "usuario_permissoes",
        "tokens_recuperacao_senha",
        "sessoes_operacoes",
        "usuarios_operacoes",
        "convites_organizacao",
        "credenciais_integracao",
        "dominios_organizacao",
    ):
        op.execute(f'DROP POLICY IF EXISTS tenant_write ON "{tabela}"')
        op.execute(f'DROP POLICY IF EXISTS tenant_bootstrap_read ON "{tabela}"')
        op.execute(f'ALTER TABLE "{tabela}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabela}" DISABLE ROW LEVEL SECURITY')

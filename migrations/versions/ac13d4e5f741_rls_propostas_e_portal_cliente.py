"""habilita RLS em propostas comerciais e na familia do portal do cliente

Revision ID: ac13d4e5f741
Revises: ab12c3d4e630

Achado da auditoria do CRM: propostas_comerciais e toda a familia de tabelas
do portal do cliente nunca receberam RLS -- ficavam protegidas so pelo filtro
manual em Python. As tabelas usadas no fluxo de login/recuperacao/proposta
publica (antes de resolver o tenant a partir do token/e-mail) usam o mesmo
padrao de "leitura de bootstrap" ja usado para tabelas de autenticacao em
l92d4b8c3e75_complete_tenant_rls.py -- SELECT liberado quando nenhum tenant
foi definido ainda na sessao, ESCRITA sempre exige o tenant ja resolvido.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ac13d4e5f741"
down_revision: str | None = "ab12c3d4e630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"
BOOTSTRAP = "NULLIF(current_setting('app.organizacao_id', true), '') IS NULL"


def _direta(tabela: str) -> None:
    """Tabela com organizacao_id proprio, sempre acessada com o tenant ja resolvido."""
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{tabela}" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )


def _direta_bootstrap(tabela: str) -> None:
    """Tabela com organizacao_id proprio, mas lida antes de o tenant ser conhecido
    (login por e-mail, link publico por token) -- SELECT sob bootstrap, escrita
    exige tenant resolvido."""
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


def _derivada_cliente(tabela: str, bootstrap: bool) -> None:
    """Tabela sem organizacao_id proprio -- isolamento derivado via cliente_id
    -> clientes_portal.organizacao_id (mesmo padrao de _derivada_usuario em
    l92d4b8c3e75, aplicado ao cliente do portal em vez do operador)."""
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    acesso = (
        "EXISTS (SELECT 1 FROM clientes_portal c WHERE c.id = cliente_id "
        f"AND ({SUPER} OR c.organizacao_id = {TENANT}))"
    )
    if bootstrap:
        op.execute(
            f'CREATE POLICY tenant_bootstrap_read ON "{tabela}" FOR SELECT '
            f'USING ({BOOTSTRAP} OR {SUPER} OR {acesso})'
        )
        op.execute(
            f'CREATE POLICY tenant_write ON "{tabela}" FOR ALL '
            f'USING ({SUPER} OR {acesso}) WITH CHECK ({SUPER} OR {acesso})'
        )
    else:
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{tabela}" '
            f"USING ({SUPER} OR {acesso}) WITH CHECK ({SUPER} OR {acesso})"
        )


def upgrade() -> None:
    # Lida por token publico (visualizar/aceitar proposta) antes de o tenant
    # ser resolvido -- ver aplicar_contexto_tenant adicionado em leads.py.
    _direta_bootstrap("propostas_comerciais")
    # Escrita sempre acontece depois de aplicar_contexto_tenant nesta mesma
    # requisicao (leads.py); leitura nunca acontece antes disso.
    _direta("assinaturas_propostas_comerciais")

    # Login e recuperacao de senha buscam o cliente por e-mail/token antes de
    # conhecer o tenant.
    _direta_bootstrap("clientes_portal")
    _derivada_cliente("sessoes_clientes_portal", bootstrap=True)
    _derivada_cliente("recuperacoes_clientes_portal", bootstrap=True)

    # Só acessadas via ClientDep (tenant ja resolvido) ou por operador autenticado.
    _direta("arquivos_clientes_portal")
    _direta("mensagens_clientes_portal")
    _derivada_cliente("notificacoes_clientes_portal", bootstrap=False)


def downgrade() -> None:
    for tabela in (
        "notificacoes_clientes_portal",
        "mensagens_clientes_portal",
        "arquivos_clientes_portal",
        "recuperacoes_clientes_portal",
        "sessoes_clientes_portal",
        "clientes_portal",
        "assinaturas_propostas_comerciais",
        "propostas_comerciais",
    ):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{tabela}"')
        op.execute(f'DROP POLICY IF EXISTS tenant_write ON "{tabela}"')
        op.execute(f'DROP POLICY IF EXISTS tenant_bootstrap_read ON "{tabela}"')
        op.execute(f'ALTER TABLE "{tabela}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabela}" DISABLE ROW LEVEL SECURITY')

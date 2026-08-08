"""enable tenant row level security

Revision ID: f36d8b2a7c11
Revises: e25c9a7b4d10
Create Date: 2026-08-07 17:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f36d8b2a7c11"
down_revision: str | Sequence[str] | None = "e25c9a7b4d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def _direta(tabela: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{tabela}" '
        f'USING ({SUPER} OR organizacao_id = {TENANT}) '
        f'WITH CHECK ({SUPER} OR organizacao_id = {TENANT})'
    )


def _derivada(tabela: str, pesquisa_sql: str) -> None:
    op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
    expressao = f"{SUPER} OR EXISTS ({pesquisa_sql})"
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{tabela}" '
        f'USING ({expressao}) WITH CHECK ({expressao})'
    )


def upgrade() -> None:
    _direta("leads")
    _direta("pesquisas_marca")

    op.execute('ALTER TABLE "eventos_auditoria" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "eventos_auditoria" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_audit_read ON "eventos_auditoria" FOR SELECT '
        f'USING ({SUPER} OR organizacao_id = {TENANT})'
    )
    op.execute(
        'CREATE POLICY tenant_audit_insert ON "eventos_auditoria" FOR INSERT '
        'WITH CHECK (true)'
    )

    pesquisa = (
        "SELECT 1 FROM pesquisas_marca p "
        f"WHERE p.id = pesquisa_id AND ({SUPER} OR p.organizacao_id = {TENANT})"
    )
    _derivada("avaliacoes_risco_marca", pesquisa)
    _derivada("previsoes_registrabilidade", pesquisa)
    _derivada("versoes_relatorio_marca", pesquisa)
    explicacao = (
        "SELECT 1 FROM avaliacoes_risco_marca a "
        "JOIN pesquisas_marca p ON p.id = a.pesquisa_id "
        "WHERE a.id = avaliacao_risco_id "
        f"AND ({SUPER} OR p.organizacao_id = {TENANT})"
    )
    _derivada("explicacoes_risco_ia", explicacao)
    op.execute("GRANT CONNECT ON DATABASE inpi TO inpi_app")
    op.execute("GRANT USAGE ON SCHEMA public TO inpi_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO inpi_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO inpi_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO inpi_app"
    )


def downgrade() -> None:
    for tabela in (
        "explicacoes_risco_ia",
        "versoes_relatorio_marca",
        "previsoes_registrabilidade",
        "avaliacoes_risco_marca",
    ):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{tabela}"')
        op.execute(f'ALTER TABLE "{tabela}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabela}" DISABLE ROW LEVEL SECURITY')
    op.execute('DROP POLICY IF EXISTS tenant_audit_insert ON "eventos_auditoria"')
    op.execute('DROP POLICY IF EXISTS tenant_audit_read ON "eventos_auditoria"')
    op.execute('ALTER TABLE "eventos_auditoria" NO FORCE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "eventos_auditoria" DISABLE ROW LEVEL SECURITY')
    for tabela in ("pesquisas_marca", "leads"):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{tabela}"')
        op.execute(f'ALTER TABLE "{tabela}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabela}" DISABLE ROW LEVEL SECURITY')

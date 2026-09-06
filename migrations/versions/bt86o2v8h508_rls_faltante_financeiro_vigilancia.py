"""habilita RLS em 8 tabelas de tenant que ficaram sem politica (achado D2)

Revision ID: bt86o2v8h508
Revises: at75n1u9j286

Achado D2 da auditoria de 06/09/2026 (docs/... auditoria CRM completa):
levantamento manual encontrou 8 tabelas com organizacao_id criadas sem
ENABLE/FORCE ROW LEVEL SECURITY nem CREATE POLICY, ao contrario de ~80
outras tabelas de negocio que ja seguem esse padrao (ver
zw99r4v0p286_cadencias.py, hk08e5rgstck_supressao_prospeccao_opt_out.py,
entre dezenas de outras). Sem RLS, o isolamento entre organizacoes nessas
8 tabelas dependia 100% de um filtro manual `organizacao_id == X` em cada
endpoint -- exatamente o tipo de esquecimento que RLS existe para eliminar
estruturalmente.

Tabelas cobertas por este achado:
- servicos_financeiros, contratacoes_servicos (financeiro)
- recibos_financeiros, renovacoes_financeiras (financeiro)
- preferencias_vigilancia, colidencias_vigilancia,
  vigilancia_execucoes, historico_alertas_vigilancia (vigilancia/portal)

So habilita a politica -- nenhuma coluna, tabela ou dado e alterado.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "bt86o2v8h508"
down_revision: str | None = "at75n1u9j286"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"

TABELAS = (
    "servicos_financeiros",
    "contratacoes_servicos",
    "recibos_financeiros",
    "renovacoes_financeiras",
    "preferencias_vigilancia",
    "colidencias_vigilancia",
    "vigilancia_execucoes",
    "historico_alertas_vigilancia",
)


def upgrade() -> None:
    for tabela in TABELAS:
        op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{tabela}" '
            f"USING ({SUPER} OR organizacao_id = {TENANT}) "
            f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
        )
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{tabela}" TO inpi_app')


def downgrade() -> None:
    for tabela in TABELAS:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{tabela}"')
        op.execute(f'ALTER TABLE "{tabela}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabela}" DISABLE ROW LEVEL SECURITY')

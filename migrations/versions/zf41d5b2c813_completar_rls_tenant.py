"""Completa o RLS de isolamento por escritório em tabelas que ficaram sem.

Achado do teste de segurança (30/09/2026): 3 tabelas com organizacao_id
estavam sem RLS (assinaturas_documentos_lead, versoes_documentos_lead,
modelos_ranking_busca) e 11 tinham RLS habilitado mas SEM FORCE -- ou seja,
o dono da tabela ignorava a política. Esta migration liga RLS+FORCE+política
nas 3 e adiciona FORCE nas 11, no mesmo padrão (tenant_isolation) das demais.

feature_flags_eventos fica de fora de propósito: é telemetria de plataforma,
com organizacao_id opcional (eventos sem organização), e uma política de
isolamento estrita descartaria esses registros.

Observação: enquanto o usuário de banco da aplicação for superusuário/BYPASSRLS,
nenhuma política de RLS é aplicada em produção -- a correção definitiva é
apontar a aplicação para um usuário sem esses poderes (feito à parte).

Revision ID: zf41d5b2c813
Revises: ze30c4a1b702
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zf41d5b2c813"
down_revision: str | None = "ze30c4a1b702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"

# Sem RLS nenhum: ligar RLS + FORCE + política de isolamento.
SEM_RLS = ("assinaturas_documentos_lead", "versoes_documentos_lead", "modelos_ranking_busca")

# RLS ligado mas sem FORCE (já têm a política tenant_isolation): só faltava o FORCE.
SEM_FORCE = (
    "ativos_partes_pi",
    "ativos_pi",
    "ativos_processos_pi",
    "centros_custo_financeiros",
    "contratos_juridicos",
    "custos_juridicos",
    "departamentos_financeiros",
    "documentos_ativos_pi",
    "fornecedores_juridicos",
    "projetos_busca_marca",
    "webhooks_financeiros",
)


def upgrade() -> None:
    for tabela in SEM_RLS:
        op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{tabela}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{tabela}" '
            f"USING ({SUPER} OR organizacao_id = {TENANT}) "
            f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
        )
    for tabela in SEM_FORCE:
        op.execute(f'ALTER TABLE "{tabela}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    for tabela in SEM_FORCE:
        op.execute(f'ALTER TABLE "{tabela}" NO FORCE ROW LEVEL SECURITY')
    for tabela in SEM_RLS:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{tabela}"')
        op.execute(f'ALTER TABLE "{tabela}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{tabela}" DISABLE ROW LEVEL SECURITY')

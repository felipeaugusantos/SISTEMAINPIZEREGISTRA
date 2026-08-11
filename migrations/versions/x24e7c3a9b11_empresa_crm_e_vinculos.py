"""empresa crm and explicit contact/research links

Revision ID: x24e7c3a9b11
Revises: w23d6a1f2e90
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "x24e7c3a9b11"
down_revision: str | None = "w23d6a1f2e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "empresas_crm",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("nome_normalizado", sa.String(length=200), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organizacao_id", "nome_normalizado", name="uq_empresa_crm_org_nome"
        ),
    )
    op.create_index("ix_empresas_crm_organizacao_id", "empresas_crm", ["organizacao_id"])
    op.create_index("ix_empresas_crm_nome", "empresas_crm", ["nome"])
    op.create_index("ix_empresas_crm_nome_normalizado", "empresas_crm", ["nome_normalizado"])
    op.execute('ALTER TABLE "empresas_crm" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "empresas_crm" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "empresas_crm" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "empresas_crm" TO inpi_app')
    op.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app')

    op.add_column("leads", sa.Column("empresa_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_leads_empresa_crm", "leads", "empresas_crm", ["empresa_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_leads_empresa_id", "leads", ["empresa_id"])
    op.add_column("pesquisas_marca", sa.Column("empresa_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_pesquisas_empresa_crm",
        "pesquisas_marca",
        "empresas_crm",
        ["empresa_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_pesquisa_original",
        "pesquisas_marca",
        "pesquisas_marca",
        ["pesquisa_original_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_pesquisas_marca_empresa_id", "pesquisas_marca", ["empresa_id"])
    op.add_column("contatos_lead", sa.Column("empresa_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_contatos_empresa_crm",
        "contatos_lead",
        "empresas_crm",
        ["empresa_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_contatos_lead_empresa_id", "contatos_lead", ["empresa_id"])

    op.execute(
        """
        INSERT INTO empresas_crm (organizacao_id, nome, nome_normalizado)
        SELECT DISTINCT ON (organizacao_id, nome_normalizado)
               organizacao_id, nome, nome_normalizado
        FROM (
          SELECT organizacao_id,
                 regexp_replace(trim(empresa), '\\s+', ' ', 'g') AS nome,
                 lower(regexp_replace(trim(immutable_unaccent(empresa)), '\\s+', ' ', 'g'))
                   AS nome_normalizado
          FROM leads
          WHERE trim(COALESCE(empresa, '')) <> ''
        ) AS candidatas
        ORDER BY organizacao_id, nome_normalizado, nome
        ON CONFLICT (organizacao_id, nome_normalizado) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE leads AS lead
        SET empresa_id = empresa.id
        FROM empresas_crm AS empresa
        WHERE empresa.organizacao_id = lead.organizacao_id
          AND empresa.nome_normalizado = lower(
            regexp_replace(trim(immutable_unaccent(lead.empresa)), '\\s+', ' ', 'g')
          )
        """
    )
    op.execute(
        """
        UPDATE pesquisas_marca AS pesquisa
        SET empresa_id = lead.empresa_id
        FROM leads AS lead
        WHERE lead.id = pesquisa.lead_id
        """
    )
    op.execute(
        """
        UPDATE contatos_lead AS contato
        SET empresa_id = lead.empresa_id
        FROM leads AS lead
        WHERE lead.id = contato.lead_id
        """
    )
    op.execute(
        """
        UPDATE contatos_lead AS contato
        SET pesquisa_id = unica.pesquisa_id
        FROM (
          SELECT lead_id, min(id) AS pesquisa_id
          FROM pesquisas_marca
          WHERE lead_id IS NOT NULL
          GROUP BY lead_id
          HAVING count(*) = 1
        ) AS unica
        WHERE contato.lead_id = unica.lead_id
          AND contato.pesquisa_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_contatos_lead_empresa_id", table_name="contatos_lead")
    op.drop_constraint("fk_contatos_empresa_crm", "contatos_lead", type_="foreignkey")
    op.drop_column("contatos_lead", "empresa_id")
    op.drop_index("ix_pesquisas_marca_empresa_id", table_name="pesquisas_marca")
    op.drop_constraint("fk_pesquisa_original", "pesquisas_marca", type_="foreignkey")
    op.drop_constraint("fk_pesquisas_empresa_crm", "pesquisas_marca", type_="foreignkey")
    op.drop_column("pesquisas_marca", "empresa_id")
    op.drop_index("ix_leads_empresa_id", table_name="leads")
    op.drop_constraint("fk_leads_empresa_crm", "leads", type_="foreignkey")
    op.drop_column("leads", "empresa_id")
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "empresas_crm"')
    op.drop_table("empresas_crm")

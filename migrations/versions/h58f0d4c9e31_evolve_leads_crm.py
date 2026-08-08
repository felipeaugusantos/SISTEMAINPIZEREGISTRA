"""evolve leads into an auditable lightweight CRM

Revision ID: h58f0d4c9e31
Revises: g47e9c3b8d22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h58f0d4c9e31"
down_revision: str | None = "g47e9c3b8d22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE leads DROP CONSTRAINT IF EXISTS status_lead")
    op.execute(
        "ALTER TABLE leads ADD CONSTRAINT status_lead CHECK "
        "(status IN ('novo','em_contato','qualificado','proposta_enviada',"
        "'sem_retorno','convertido','descartado'))"
    )
    op.add_column("leads", sa.Column("responsavel_id", sa.BigInteger(), nullable=True))
    op.add_column("leads", sa.Column("notas", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("proxima_acao_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "leads", sa.Column("ultimo_contato_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("leads", sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("leads", sa.Column("arquivado_em", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_leads_responsavel_id_usuarios_operacoes",
        "leads",
        "usuarios_operacoes",
        ["responsavel_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for coluna in ("responsavel_id", "proxima_acao_em", "arquivado_em"):
        op.create_index(f"ix_leads_{coluna}", "leads", [coluna])

    # Relatorios e pesquisas pertencem a organizacao e sobrevivem ao arquivamento do contato.
    op.drop_constraint("pesquisas_marca_lead_id_fkey", "pesquisas_marca", type_="foreignkey")
    op.alter_column("pesquisas_marca", "lead_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_foreign_key(
        "fk_pesquisas_marca_lead_id_leads",
        "pesquisas_marca",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Consolida contatos repetidos por e-mail e transfere o historico ao primeiro cadastro.
    op.execute(
        """
        WITH repetidos AS (
          SELECT id, MIN(id) OVER (PARTITION BY organizacao_id, LOWER(email)) AS principal
          FROM leads WHERE arquivado_em IS NULL
        )
        UPDATE pesquisas_marca p SET lead_id = r.principal
        FROM repetidos r WHERE p.lead_id = r.id AND r.id <> r.principal
        """
    )
    op.execute(
        """
        WITH repetidos AS (
          SELECT id, MIN(id) OVER (PARTITION BY organizacao_id, LOWER(email)) AS principal
          FROM leads WHERE arquivado_em IS NULL
        )
        UPDATE solicitacoes_privacidade s SET lead_id = r.principal
        FROM repetidos r WHERE s.lead_id = r.id AND r.id <> r.principal
        """
    )
    op.execute(
        """
        WITH repetidos AS (
          SELECT id, ROW_NUMBER() OVER (
            PARTITION BY organizacao_id, LOWER(email) ORDER BY id
          ) AS ordem
          FROM leads WHERE arquivado_em IS NULL
        )
        DELETE FROM leads l USING repetidos r WHERE l.id = r.id AND r.ordem > 1
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_leads_org_email_ativos "
        "ON leads (organizacao_id, LOWER(email)) WHERE arquivado_em IS NULL"
    )


def downgrade() -> None:
    op.drop_index("uq_leads_org_email_ativos", table_name="leads")
    op.drop_constraint("fk_pesquisas_marca_lead_id_leads", "pesquisas_marca", type_="foreignkey")
    op.alter_column("pesquisas_marca", "lead_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_foreign_key(
        "pesquisas_marca_lead_id_fkey",
        "pesquisas_marca",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="CASCADE",
    )
    for coluna in ("arquivado_em", "proxima_acao_em", "responsavel_id"):
        op.drop_index(f"ix_leads_{coluna}", table_name="leads")
    op.drop_constraint("fk_leads_responsavel_id_usuarios_operacoes", "leads", type_="foreignkey")
    colunas = (
        "arquivado_em", "tags", "ultimo_contato_em",
        "proxima_acao_em", "notas", "responsavel_id",
    )
    for coluna in colunas:
        op.drop_column("leads", coluna)
    op.execute("ALTER TABLE leads DROP CONSTRAINT IF EXISTS status_lead")
    op.execute(
        "ALTER TABLE leads ADD CONSTRAINT status_lead CHECK "
        "(status IN ('novo','em_contato','convertido','descartado'))"
    )

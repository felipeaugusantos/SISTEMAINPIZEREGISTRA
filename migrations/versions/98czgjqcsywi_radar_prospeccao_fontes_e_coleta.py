"""radar de prospeccao: fontes, campanhas e cache RFB (Fase 2)

Revision ID: 98czgjqcsywi
Revises: bsllcopj8crg

Fase 2 do roadmap do Radar de Prospeccao (03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md): fonte escolhida foi os
Dados Abertos do CNPJ (Receita Federal, gratuito). Como o arquivo da RFB nao
e particionado por UF/CNAE (sao ~10 arquivos arbitrarios por tipo, sem
filtro possivel na origem), a unica forma pratica de "coletar por campanha"
com resposta rapida e manter um cache local (nao por tenant -- e dado
publico, compartilhavel entre organizacoes do SaaS) atualizado por um script
de ETL a parte (app/cli/importar_cnpj_rfb.py, rodado por cron, fora do
request-response da aplicacao), e consultar esse cache por campanha.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "98czgjqcsywi"
down_revision: str | None = "bsllcopj8crg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"

TIPOS_FONTE = ("cnae_publico", "importacao_manual")
STATUS_CAMPANHA = ("rascunho", "ativa", "pausada", "concluida")


def upgrade() -> None:
    op.create_table(
        "prospect_fontes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("configuracao", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "tipo IN (" + ", ".join(f"'{v}'" for v in TIPOS_FONTE) + ")", name="ck_prospect_fontes_tipo_valido"
        ),
    )
    op.create_index("ix_prospect_fontes_organizacao_id", "prospect_fontes", ["organizacao_id"])
    op.execute('ALTER TABLE "prospect_fontes" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "prospect_fontes" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "prospect_fontes" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "prospect_fontes" TO inpi_app')

    op.create_table(
        "campanhas_prospeccao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("criterios_busca", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="rascunho"),
        sa.Column("meta_prospects", sa.Integer(), nullable=True),
        sa.Column("criado_por", sa.String(length=150), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("encerrada_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{v}'" for v in STATUS_CAMPANHA) + ")",
            name="ck_campanhas_prospeccao_status_valido",
        ),
    )
    op.create_index("ix_campanhas_prospeccao_organizacao_id", "campanhas_prospeccao", ["organizacao_id"])
    op.create_index("ix_campanhas_prospeccao_status", "campanhas_prospeccao", ["status"])
    op.execute('ALTER TABLE "campanhas_prospeccao" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "campanhas_prospeccao" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "campanhas_prospeccao" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "campanhas_prospeccao" TO inpi_app')

    op.add_column("prospects", sa.Column("fonte_id", sa.BigInteger(), nullable=True))
    op.add_column("prospects", sa.Column("campanha_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_prospects_fonte_id", "prospects", "prospect_fontes", ["fonte_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_prospects_campanha_id", "prospects", "campanhas_prospeccao", ["campanha_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_prospects_fonte_id", "prospects", ["fonte_id"])
    op.create_index("ix_prospects_campanha_id", "prospects", ["campanha_id"])

    # Cache nacional de estabelecimentos (Dados Abertos do CNPJ/RFB) -- NAO e
    # por tenant (dado publico compartilhavel entre organizacoes do SaaS) e
    # por isso NAO tem RLS. Alimentado por app/cli/importar_cnpj_rfb.py.
    op.create_table(
        "cache_estabelecimentos_rfb",
        sa.Column("cnpj", sa.String(length=14), nullable=False),
        sa.Column("razao_social", sa.String(length=200), nullable=False),
        sa.Column("nome_fantasia", sa.String(length=200), nullable=True),
        sa.Column("cnae_principal", sa.String(length=10), nullable=True),
        sa.Column("cnaes_secundarios", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("porte", sa.String(length=20), nullable=True),
        sa.Column("situacao_cadastral", sa.String(length=20), nullable=True),
        sa.Column("data_abertura", sa.Date(), nullable=True),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("cidade", sa.String(length=120), nullable=True),
        sa.Column("telefone", sa.String(length=30), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("cnpj"),
    )
    op.create_index("ix_cache_estabelecimentos_rfb_cnae_principal", "cache_estabelecimentos_rfb", ["cnae_principal"])
    op.create_index("ix_cache_estabelecimentos_rfb_situacao_cadastral", "cache_estabelecimentos_rfb", ["situacao_cadastral"])
    op.create_index("ix_cache_estabelecimentos_rfb_data_abertura", "cache_estabelecimentos_rfb", ["data_abertura"])
    op.create_index("ix_cache_estabelecimentos_rfb_uf_cidade", "cache_estabelecimentos_rfb", ["uf", "cidade"])
    op.create_index("ix_cache_estabelecimentos_rfb_cnae_uf", "cache_estabelecimentos_rfb", ["cnae_principal", "uf"])


def downgrade() -> None:
    op.drop_table("cache_estabelecimentos_rfb")
    op.drop_index("ix_prospects_campanha_id", table_name="prospects")
    op.drop_index("ix_prospects_fonte_id", table_name="prospects")
    op.drop_constraint("fk_prospects_campanha_id", "prospects", type_="foreignkey")
    op.drop_constraint("fk_prospects_fonte_id", "prospects", type_="foreignkey")
    op.drop_column("prospects", "campanha_id")
    op.drop_column("prospects", "fonte_id")
    op.drop_table("campanhas_prospeccao")
    op.drop_table("prospect_fontes")

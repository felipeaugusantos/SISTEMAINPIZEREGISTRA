"""add production governance

Revision ID: f50b91a74e21
Revises: e92f7a14c805
Create Date: 2026-07-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f50b91a74e21"
down_revision: str | Sequence[str] | None = "e92f7a14c805"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "explicacoes_risco_ia",
        sa.Column("duracao_ms", sa.Integer(), nullable=True),
    )
    op.create_table(
        "controle_producao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ia_habilitada", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ia_rollout_percentual", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("atualizado_por", sa.String(length=150), nullable=True),
        sa.Column("justificativa", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "ia_rollout_percentual BETWEEN 0 AND 100",
            name="ck_controle_producao_rollout",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO controle_producao "
            "(id, ia_habilitada, ia_rollout_percentual) VALUES (1, false, 0)"
        )
    )
    op.create_table(
        "versoes_relatorio_marca",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pesquisa_id", sa.String(length=36), nullable=False),
        sa.Column("numero_versao", sa.Integer(), nullable=False),
        sa.Column("schema_versao", sa.String(length=30), nullable=False),
        sa.Column("conteudo_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "gerado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["pesquisa_id"],
            ["pesquisas_marca.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pesquisa_id",
            "numero_versao",
            name="uq_versoes_relatorio_marca_pesquisa_numero",
        ),
    )
    op.create_index(
        "ix_versoes_relatorio_marca_pesquisa_id",
        "versoes_relatorio_marca",
        ["pesquisa_id"],
    )
    op.create_index(
        "ix_versoes_relatorio_marca_schema_versao",
        "versoes_relatorio_marca",
        ["schema_versao"],
    )
    op.create_index(
        "ix_versoes_relatorio_marca_conteudo_hash",
        "versoes_relatorio_marca",
        ["conteudo_hash"],
    )
    op.create_index(
        "ix_versoes_relatorio_marca_gerado_em",
        "versoes_relatorio_marca",
        ["gerado_em"],
    )
    op.create_table(
        "eventos_operacionais",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("componente", sa.String(length=40), nullable=False),
        sa.Column("operacao", sa.String(length=150), nullable=False),
        sa.Column("sucesso", sa.Boolean(), nullable=False),
        sa.Column("duracao_ms", sa.Integer(), nullable=False),
        sa.Column("status_http", sa.Integer(), nullable=False),
        sa.Column("codigo_erro", sa.String(length=100), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("componente", "operacao", "sucesso", "status_http", "codigo_erro", "criado_em"):
        op.create_index(f"ix_eventos_operacionais_{coluna}", "eventos_operacionais", [coluna])
    op.create_table(
        "eventos_auditoria",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ator", sa.String(length=150), nullable=False),
        sa.Column("acao", sa.String(length=20), nullable=False),
        sa.Column("recurso", sa.String(length=180), nullable=False),
        sa.Column("sucesso", sa.Boolean(), nullable=False),
        sa.Column("status_http", sa.Integer(), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("detalhes", sa.JSON(), nullable=False),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in ("ator", "acao", "recurso", "sucesso", "criado_em"):
        op.create_index(f"ix_eventos_auditoria_{coluna}", "eventos_auditoria", [coluna])


def downgrade() -> None:
    for coluna in ("ator", "acao", "recurso", "sucesso", "criado_em"):
        op.drop_index(f"ix_eventos_auditoria_{coluna}", table_name="eventos_auditoria")
    op.drop_table("eventos_auditoria")
    for coluna in ("componente", "operacao", "sucesso", "status_http", "codigo_erro", "criado_em"):
        op.drop_index(
            f"ix_eventos_operacionais_{coluna}",
            table_name="eventos_operacionais",
        )
    op.drop_table("eventos_operacionais")
    op.drop_index(
        "ix_versoes_relatorio_marca_gerado_em",
        table_name="versoes_relatorio_marca",
    )
    op.drop_index(
        "ix_versoes_relatorio_marca_conteudo_hash",
        table_name="versoes_relatorio_marca",
    )
    op.drop_index(
        "ix_versoes_relatorio_marca_schema_versao",
        table_name="versoes_relatorio_marca",
    )
    op.drop_index(
        "ix_versoes_relatorio_marca_pesquisa_id",
        table_name="versoes_relatorio_marca",
    )
    op.drop_table("versoes_relatorio_marca")
    op.drop_table("controle_producao")
    op.drop_column("explicacoes_risco_ia", "duracao_ms")

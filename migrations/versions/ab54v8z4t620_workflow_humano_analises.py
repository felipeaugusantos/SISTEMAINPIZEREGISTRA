"""workflow humano formal para análises e relatórios

Revision ID: ab54v8z4t620
Revises: zz32u7y3s519
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ab54v8z4t620"
down_revision: str | None = "zz32u7y3s519"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pesquisas_marca",
        sa.Column("analysis_state", sa.String(length=30), nullable=False, server_default="DRAFT"),
    )
    op.add_column(
        "pesquisas_marca", sa.Column("validated_by", sa.String(length=150), nullable=True)
    )
    op.add_column(
        "pesquisas_marca", sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("pesquisas_marca", sa.Column("analysis_notes", sa.Text(), nullable=True))
    op.create_index(
        "ix_pesquisas_marca_analysis_state", "pesquisas_marca", ["analysis_state"]
    )
    op.create_index(
        "ix_pesquisas_marca_validated_at", "pesquisas_marca", ["validated_at"]
    )
    op.create_check_constraint(
        "ck_pesquisas_marca_analysis_state",
        "pesquisas_marca",
        "analysis_state IN ('DRAFT', 'PENDING_REVIEW', 'IN_REVIEW', "
        "'CHANGES_REQUESTED', 'VALIDATED')",
    )

    op.add_column(
        "versoes_relatorio_marca",
        sa.Column("validated_by", sa.String(length=150), nullable=True),
    )
    op.add_column(
        "versoes_relatorio_marca",
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "versoes_relatorio_marca", sa.Column("validation_notes", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_versoes_relatorio_marca_validated_at",
        "versoes_relatorio_marca",
        ["validated_at"],
    )

    # Compatibilidade conservadora: snapshots existentes entram na fila; pareceres
    # humanos já registrados indicam revisão em andamento, nunca validação automática.
    op.execute(
        """
        UPDATE pesquisas_marca AS p
        SET analysis_state = CASE
            WHEN EXISTS (
                SELECT 1 FROM avaliacoes_risco_marca AS a
                WHERE a.pesquisa_id = p.id AND a.avaliado_em IS NOT NULL
            ) THEN 'IN_REVIEW'
            WHEN EXISTS (
                SELECT 1 FROM versoes_relatorio_marca AS v WHERE v.pesquisa_id = p.id
            ) THEN 'PENDING_REVIEW'
            ELSE 'DRAFT'
        END,
        validated_by = NULL,
        validated_at = NULL,
        analysis_notes = CASE
            WHEN relatorio_completo_gerado_em IS NOT NULL
            THEN 'Relatório legado requer validação formal após a migração da Fase 6.'
            ELSE analysis_notes
        END
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_versoes_relatorio_marca_validated_at", table_name="versoes_relatorio_marca"
    )
    op.drop_column("versoes_relatorio_marca", "validation_notes")
    op.drop_column("versoes_relatorio_marca", "validated_at")
    op.drop_column("versoes_relatorio_marca", "validated_by")

    op.drop_constraint(
        "ck_pesquisas_marca_analysis_state", "pesquisas_marca", type_="check"
    )
    op.drop_index("ix_pesquisas_marca_validated_at", table_name="pesquisas_marca")
    op.drop_index("ix_pesquisas_marca_analysis_state", table_name="pesquisas_marca")
    op.drop_column("pesquisas_marca", "analysis_notes")
    op.drop_column("pesquisas_marca", "validated_at")
    op.drop_column("pesquisas_marca", "validated_by")
    op.drop_column("pesquisas_marca", "analysis_state")

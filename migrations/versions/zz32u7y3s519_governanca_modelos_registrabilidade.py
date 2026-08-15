"""governanca formal dos modelos de registrabilidade

Revision ID: zz32u7y3s519
Revises: zy21t6x2r408
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zz32u7y3s519"
down_revision: str | None = "zy21t6x2r408"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE modelos_registrabilidade
        SET status = CASE status
            WHEN 'ativo' THEN 'ACTIVE'
            WHEN 'candidato' THEN 'SHADOW'
            WHEN 'reprovado' THEN 'DISABLED'
            WHEN 'arquivado' THEN 'DISABLED'
            WHEN 'SHADOW' THEN 'SHADOW'
            WHEN 'VALIDATION' THEN 'VALIDATION'
            WHEN 'ACTIVE' THEN 'ACTIVE'
            ELSE 'DISABLED'
        END
        """
    )
    op.alter_column(
        "modelos_registrabilidade",
        "status",
        existing_type=sa.String(length=20),
        server_default="SHADOW",
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_modelos_registrabilidade_status",
        "modelos_registrabilidade",
        "status IN ('SHADOW', 'VALIDATION', 'ACTIVE', 'DISABLED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_modelos_registrabilidade_status",
        "modelos_registrabilidade",
        type_="check",
    )
    op.execute(
        """
        UPDATE modelos_registrabilidade
        SET status = CASE status
            WHEN 'ACTIVE' THEN 'ativo'
            WHEN 'VALIDATION' THEN 'candidato'
            WHEN 'SHADOW' THEN 'candidato'
            ELSE 'arquivado'
        END
        """
    )
    op.alter_column(
        "modelos_registrabilidade",
        "status",
        existing_type=sa.String(length=20),
        server_default="candidato",
        existing_nullable=False,
    )

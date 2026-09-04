"""corrige colidencias_vigilancia: coluna atualizado_em ausente (achado FASE6-2)

Revision ID: wm10n8p2a731
Revises: uw24qat2dmwp

Achado FASE6-2 da auditoria (04/09/2026, alembic check): app/models.py
declara ColidenciaVigilancia.atualizado_em desde a criação da tabela (ver
migrations/versions/acd77v2o0y74_vigilancia_colidencias.py), mas a
migração original nunca criou essa coluna -- só criou_em. O código já
grava linhas nessa tabela (app/vigilancia.py, app/api/vigilancia.py, tarefa
periódica "vigilancia.executar_semanal") desde então: qualquer INSERT
feito pelo SQLAlchemy tenta escrever numa coluna que não existe no banco
de produção, o que quebra com um erro real de banco -- não é um risco
teórico. A mesma migração original também não criou os índices em
organizacao_id/criado_em que o modelo declara (index=True) -- adicionados
aqui também, no mesmo escopo do achado.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "wm10n8p2a731"
down_revision: str | None = "uw24qat2dmwp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "colidencias_vigilancia",
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_colidencias_vigilancia_organizacao_id", "colidencias_vigilancia", ["organizacao_id"])
    op.create_index("ix_colidencias_vigilancia_criado_em", "colidencias_vigilancia", ["criado_em"])


def downgrade() -> None:
    op.drop_index("ix_colidencias_vigilancia_criado_em", table_name="colidencias_vigilancia")
    op.drop_index("ix_colidencias_vigilancia_organizacao_id", table_name="colidencias_vigilancia")
    op.drop_column("colidencias_vigilancia", "atualizado_em")

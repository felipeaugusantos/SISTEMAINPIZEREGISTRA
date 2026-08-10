"""quarantine statistical models that fail automatic quality gates

Revision ID: u21b3e8a2c60
Revises: t20a2d7f1b59
"""

from collections.abc import Sequence

from alembic import op

revision: str = "u21b3e8a2c60"
down_revision: str | None = "t20a2d7f1b59"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE modelos_registrabilidade AS modelo
        SET status = 'reprovado'
        FROM controle_aprendizado_marca AS controle
        WHERE modelo.status = 'ativo'
          AND controle.id = 1
          AND (
            COALESCE((modelo.metricas->>'recall')::double precision, 0) < controle.minimo_recall
            OR COALESCE((modelo.metricas->>'especificidade')::double precision, 0) < controle.minimo_especificidade
            OR COALESCE((modelo.metricas->>'brier')::double precision, 1) > controle.maximo_brier
            OR COALESCE((modelo.metricas->>'ece')::double precision, 1) > controle.maximo_ece
            OR COALESCE((modelo.dataset->>'total')::integer, 0) < controle.minimo_amostras_modelo
            OR COALESCE((modelo.dataset->>'teste')::integer, 0) < controle.minimo_amostras_teste
          )
        """
    )
    op.execute(
        """
        UPDATE controle_aprendizado_marca
        SET inferencia_habilitada = false,
            exibir_cliente = false,
            justificativa = 'Modelos ativos reprovados foram colocados em quarentena automatica'
        WHERE id = 1
        """
    )


def downgrade() -> None:
    # Uma reversao nao deve reativar automaticamente um modelo reprovado.
    pass

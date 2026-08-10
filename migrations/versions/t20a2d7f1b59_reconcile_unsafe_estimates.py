"""reconcile unsafe registrability estimates

Revision ID: t20a2d7f1b59
Revises: s19f1c6e0a48
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t20a2d7f1b59"
down_revision: str | None = "s19f1c6e0a48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE previsoes_registrabilidade
        SET modo = 'sombra',
            elegivel_cliente = false,
            motivos_inelegibilidade = CASE
              WHEN COALESCE(motivos_inelegibilidade, '[]'::json)::jsonb
                   @> '["Elegibilidade reconciliada; nova validacao necessaria"]'::jsonb
              THEN motivos_inelegibilidade
              ELSE (COALESCE(motivos_inelegibilidade, '[]'::json)::jsonb
                    || '["Elegibilidade reconciliada; nova validacao necessaria"]'::jsonb)::json
            END
        WHERE elegivel_cliente = true
        """
    )
    op.execute(
        """
        UPDATE controle_aprendizado_marca
        SET exibir_cliente = false,
            justificativa = 'Exibicao suspensa para revalidacao automatica dos criterios de qualidade'
        WHERE id = 1
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE previsoes_registrabilidade
        SET motivos_inelegibilidade = (
            COALESCE(motivos_inelegibilidade, '[]'::json)::jsonb
            - 'Elegibilidade reconciliada; nova validacao necessaria'
        )::json
        """
    )

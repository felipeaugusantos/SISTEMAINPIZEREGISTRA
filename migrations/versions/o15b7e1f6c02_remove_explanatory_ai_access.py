"""Remove acessos ativos da antiga IA explicativa.

Revision ID: o15b7e1f6c02
Revises: n14f6d0e5a97
"""

from collections.abc import Sequence

from alembic import op

revision: str = "o15b7e1f6c02"
down_revision: str | Sequence[str] | None = "n14f6d0e5a97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHAVES = ("ai.view", "ai.generate", "ai.review")


def upgrade() -> None:
    chaves = ", ".join(f"'{chave}'" for chave in _CHAVES)
    op.execute(
        f"DELETE FROM usuario_permissoes WHERE permissao_id IN "
        f"(SELECT id FROM permissoes_operacoes WHERE chave IN ({chaves}))"
    )
    op.execute(f"DELETE FROM permissoes_operacoes WHERE chave IN ({chaves})")
    op.execute("UPDATE planos_saas SET modulos = (modulos::jsonb - 'ia')::json")
    op.execute(
        "UPDATE controle_producao SET ia_habilitada = false, ia_rollout_percentual = 0"
    )


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO permissoes_operacoes (chave, modulo, nome, descricao, ordem)
        VALUES
          ('ai.view', 'IA explicativa', 'Visualizar IA',
           'Consultar explicacoes assistidas.', 10),
          ('ai.generate', 'IA explicativa', 'Gerar explicacao',
           'Solicitar uma explicacao estruturada.', 11),
          ('ai.review', 'IA explicativa', 'Revisar explicacao',
           'Aprovar ou rejeitar explicacoes.', 12)
        ON CONFLICT (chave) DO NOTHING
        """
    )

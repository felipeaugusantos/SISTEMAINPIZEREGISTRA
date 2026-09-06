"""distribuicao automatica de leads: round-robin entre operadores comerciais

Revision ID: cu97p3w9i619
Revises: bt86o2v8h508

Achado item 12 da auditoria completa do CRM (06/09/2026): todo lead nascia
sem responsavel, dependendo 100% de um humano abrir o card "Sem
responsavel" no dashboard e atribuir na mao (`atribuir_ao_operador` ja
existia, mas so atribui a quem esta executando a acao no momento -- nao
serve para leads que nascem sem nenhum operador logado, como o formulario
publico ou a importacao em lote).

`ultimo_responsavel_distribuido_id` guarda o cursor do round-robin (ultimo
operador que recebeu um lead pela distribuicao automatica) para o proximo
ciclo continuar de onde parou, mesmo entre reinicios do processo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cu97p3w9i619"
down_revision: str | None = "bt86o2v8h508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "politicas_crm",
        sa.Column("distribuicao_automatica_ativa", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "politicas_crm",
        sa.Column("ultimo_responsavel_distribuido_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_politicas_crm_ultimo_responsavel_distribuido",
        "politicas_crm",
        "usuarios_operacoes",
        ["ultimo_responsavel_distribuido_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_politicas_crm_ultimo_responsavel_distribuido", "politicas_crm", type_="foreignkey")
    op.drop_column("politicas_crm", "ultimo_responsavel_distribuido_id")
    op.drop_column("politicas_crm", "distribuicao_automatica_ativa")

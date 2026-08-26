"""separa os modulos operacionais por tenant e plano

Revision ID: aa41b8c6d702
Revises: aab14c7d5e61
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "aa41b8c6d702"
down_revision: str | None = "aab14c7d5e61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Mescla os modulos operacionais nos planos existentes sem duplicar."""
    tabela = sa.table(
        "planos_saas",
        sa.column("id", sa.BigInteger),
        sa.column("modulos", sa.JSON),
    )
    conexao = op.get_bind()
    registros = conexao.execute(sa.select(tabela.c.id, tabela.c.modulos)).all()
    novos = {"crm", "processos_monitorados", "operacao_juridica"}
    for plano_id, modulos in registros:
        atuais = list(modulos or [])
        atualizados = atuais + [modulo for modulo in novos if modulo not in atuais]
        if atualizados != atuais:
            conexao.execute(
                tabela.update().where(tabela.c.id == plano_id).values(modulos=atualizados)
            )


def downgrade() -> None:
    # Nao remover entitlements: os planos podem ter sido customizados depois.
    pass

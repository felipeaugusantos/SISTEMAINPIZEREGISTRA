"""adiciona tipo "melhoria" ao changelog de versoes do sistema

Revision ID: s75i2j0v520
Revises: r64f0h8t408

Pedido do usuário: separar a Central de atualizações por categoria
(Nova funcionalidade / Correção / Melhoria). Hoje só existem 3 tipos
("critica", "correcao", "funcionalidade") -- "melhoria" nunca existiu.
Adiciona o valor à constraint sem afetar as 28 versões já publicadas
(nenhuma delas muda de tipo retroativamente).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s75i2j0v520"
down_revision: str | None = "r64f0h8t408"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIPOS_ANTIGOS = ("critica", "correcao", "funcionalidade")
TIPOS_NOVOS = ("critica", "correcao", "melhoria", "funcionalidade")


def upgrade() -> None:
    op.drop_constraint("ck_versao_sistema_tipo", "versoes_sistema", type_="check")
    tipos_sql = ", ".join(f"'{t}'" for t in TIPOS_NOVOS)
    op.create_check_constraint("ck_versao_sistema_tipo", "versoes_sistema", f"tipo_atualizacao IN ({tipos_sql})")


def downgrade() -> None:
    op.drop_constraint("ck_versao_sistema_tipo", "versoes_sistema", type_="check")
    tipos_sql = ", ".join(f"'{t}'" for t in TIPOS_ANTIGOS)
    op.create_check_constraint("ck_versao_sistema_tipo", "versoes_sistema", f"tipo_atualizacao IN ({tipos_sql})")

"""adiciona procurador_manual em processos_monitorados (correcao de dado compartilhado)

Revision ID: v19m4n2z742
Revises: t86j3k1w631

Achado da analise do modulo de Processos monitorados (13/09/2026): o campo
"procurador" editado na tela da carteira era gravado direto em
processos.procurador -- tabela compartilhada da base nacional da RPI, sem
organizacao_id. Como duas organizacoes podem monitorar o mesmo processo, a
correcao feita por uma organizacao mudava o que a outra via, e a proxima
sincronizacao da RPI podia sobrescrever a correcao silenciosamente (ver
app/rpi/importer.py, so preserva o valor manual quando a publicacao nao
traz procurador). Esta migration adiciona uma coluna propria do vinculo
(processos_monitorados.procurador_manual), escopada por organizacao_id,
para guardar essa correcao sem tocar no dado compartilhado.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v19m4n2z742"
down_revision: str | None = "t86j3k1w631"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processos_monitorados", sa.Column("procurador_manual", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("processos_monitorados", "procurador_manual")

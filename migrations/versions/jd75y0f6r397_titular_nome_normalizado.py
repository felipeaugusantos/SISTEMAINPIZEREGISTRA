"""titular nome_normalizado (checagem ampla de titularidade na prospeccao)

Revision ID: jd75y0f6r397
Revises: ic64x9e5q286

Coluna indexada com o mesmo padrao ja usado em EmpresaCRM.nome_normalizado
(migrations/versions/x24e7c3a9b11_empresa_crm_e_vinculos.py), reaproveitando
immutable_unaccent (ja disponivel via
migrations/versions/6a2c9bd4f701_enable_unaccent_search.py). Usada para
verificar se a razao social/nome fantasia de um prospect ja e titular de
QUALQUER marca no INPI (nao so a marca especifica pesquisada -- esse check
mais restrito ja existe em app/prospeccao_triagem.py).

titulares tem ~2,4 milhoes de linhas em producao: backfill via um unico
UPDATE (sem loop em Python).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "jd75y0f6r397"
down_revision: str | None = "ic64x9e5q286"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("titulares", sa.Column("nome_normalizado", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE titulares
        SET nome_normalizado = lower(regexp_replace(trim(immutable_unaccent(nome)), '\\s+', ' ', 'g'))
        WHERE nome IS NOT NULL
        """
    )
    op.create_index("ix_titulares_nome_normalizado", "titulares", ["nome_normalizado"])


def downgrade() -> None:
    op.drop_index("ix_titulares_nome_normalizado", table_name="titulares")
    op.drop_column("titulares", "nome_normalizado")

"""store cryptographic hash for portal files"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g74b9c0d1e23"
down_revision: str | Sequence[str] | None = "f63a8b9c0d12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("arquivos_clientes_portal", sa.Column("arquivo_hash", sa.String(64), nullable=True))
    op.create_index("ix_arquivos_clientes_portal_arquivo_hash", "arquivos_clientes_portal", ["arquivo_hash"])


def downgrade() -> None:
    op.drop_index("ix_arquivos_clientes_portal_arquivo_hash", table_name="arquivos_clientes_portal")
    op.drop_column("arquivos_clientes_portal", "arquivo_hash")

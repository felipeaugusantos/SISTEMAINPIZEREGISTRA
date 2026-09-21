"""arquivo real anexado aos documentos do lead (procuracao etc.)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7

Achado do usuário (21/09/2026): "Etapa bloqueada. Documentos obrigatórios
pendentes: procuração", mas não havia onde anexar o arquivo -- DocumentoLead
sempre foi só metadado (sem armazenamento de arquivo), então mesmo o
endpoint de download do portal do cliente sempre 404ava. Colunas novas
espelham MaterialMarcaCliente (caminho/content_type/tamanho/arquivo_hash),
sem dado a migrar.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documentos_lead", sa.Column("caminho", sa.Text(), nullable=True))
    op.add_column("documentos_lead", sa.Column("content_type", sa.String(length=120), nullable=True))
    op.add_column("documentos_lead", sa.Column("tamanho", sa.BigInteger(), nullable=True))
    op.add_column("documentos_lead", sa.Column("arquivo_hash", sa.String(length=64), nullable=True))
    op.create_index(
        op.f("ix_documentos_lead_arquivo_hash"), "documentos_lead", ["arquivo_hash"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_documentos_lead_arquivo_hash"), table_name="documentos_lead")
    op.drop_column("documentos_lead", "arquivo_hash")
    op.drop_column("documentos_lead", "tamanho")
    op.drop_column("documentos_lead", "content_type")
    op.drop_column("documentos_lead", "caminho")

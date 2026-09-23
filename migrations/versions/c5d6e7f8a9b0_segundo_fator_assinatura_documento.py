"""evidência de segundo fator em assinaturas de documento

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9

Achado do Codex no PR #120 (Fase 13.2 da auditoria fina do Portal do
Cliente, 23/09/2026): assinar_documento_portal passou a exigir um
código de confirmação por e-mail, mas AssinaturaDocumentoLead nunca teve
os campos segundo_fator_canal/segundo_fator_confirmado_em que
AssinaturaPropostaComercial já usa pra essa mesma evidência -- uma
assinatura de documento pelo portal ficava indistinguível de uma sem
verificação nenhuma em auditoria.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assinaturas_documentos_lead", sa.Column("segundo_fator_canal", sa.String(20), nullable=True))
    op.add_column(
        "assinaturas_documentos_lead",
        sa.Column("segundo_fator_confirmado_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assinaturas_documentos_lead", "segundo_fator_confirmado_em")
    op.drop_column("assinaturas_documentos_lead", "segundo_fator_canal")

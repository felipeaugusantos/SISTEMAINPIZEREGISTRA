"""lembrete crm: motivo do adiamento

Revision ID: 4f3fdabad4d3
Revises: 562c64375107

Achado do usuário (15/09/2026): o botão "Adiar 1 dia" (lembretes do CRM)
empurrava lembrar_em sem nunca registrar por quê. Coluna nova, opcional --
guarda só o motivo do último adiamento (o histórico completo de
quem/quando já é coberto por EventoAuditoria via _auditar_lembrete).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4f3fdabad4d3"
down_revision: str | None = "562c64375107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lembretes_crm", sa.Column("motivo_adiamento", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lembretes_crm", "motivo_adiamento")

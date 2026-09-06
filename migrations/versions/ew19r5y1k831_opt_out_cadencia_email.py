"""opt-out de cadencia de e-mail (link de descadastro)

Revision ID: ew19r5y1k831
Revises: dv08q4x0j720

Achado da auditoria completa do CRM (06/09/2026, item 51 e pergunta
estrutural G): os e-mails de cadencia comercial nao tinham nenhum link de
descadastro embutido -- o unico opt-out que existia era o de prospeccao
fria (SupressaoProspeccao), sem cobrir quem ja e Lead. `cadencia_opt_out_em`
nulo (padrao) mantem o comportamento atual; setado quando o titular clica
no link de descadastro do proprio e-mail (reaproveita o token de rastreio
ja existente em envios_cadencia_email -- sem coluna nova nessa tabela nem
mudanca de politica RLS).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ew19r5y1k831"
down_revision: str | None = "dv08q4x0j720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("cadencia_opt_out_em", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "cadencia_opt_out_em")

"""e-mail de alerta de leads configurável por organização

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2

Achado da Fase 15.2 (auditoria fina de Leads, 23/09/2026): o alerta de
"lead novo sem responsável" e de "nova pesquisa" sempre mandava pra
settings.equipe_atendimento_email, uma env var GLOBAL do processo --
em um SaaS multi-tenant, todo tenant disparava alerta pra mesma caixa
(ou nenhum recebia, se a env não estivesse configurada pro tenant
certo). Nulo (padrão) mantém o fallback pra env var global.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("politicas_crm", sa.Column("email_alerta_leads", sa.String(254), nullable=True))


def downgrade() -> None:
    op.drop_column("politicas_crm", "email_alerta_leads")

"""utms e atribuicao de marketing em leads

Revision ID: hm13n5o6p762
Revises: gl02m3n4o651

Fase 7 do plano Leads/CRM (03/09/2026), achado L2: nao havia captura de UTM no
formulario publico, entao nao dava para atribuir um lead a uma campanha de
marketing especifica. Adiciona colunas de primeira origem (utm_source,
utm_medium, utm_campaign -- capturadas uma unica vez, nunca sobrescritas) e de
ultima origem (*_ultimo -- atualizadas a cada reenvio do mesmo lead).

Colunas aditivas e nullable, sem backfill -- leads existentes ficam sem UTM
retroativa (nao ha como reconstruir essa informacao).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "hm13n5o6p762"
down_revision: str | None = "gl02m3n4o651"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUNAS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_source_ultimo",
    "utm_medium_ultimo",
    "utm_campaign_ultimo",
)
COM_INDICE = ("utm_source", "utm_campaign")


def upgrade() -> None:
    for coluna in COLUNAS:
        op.add_column("leads", sa.Column(coluna, sa.String(length=100), nullable=True))
    for coluna in COM_INDICE:
        op.create_index(f"ix_leads_{coluna}", "leads", [coluna])


def downgrade() -> None:
    for coluna in COM_INDICE:
        op.drop_index(f"ix_leads_{coluna}", table_name="leads")
    for coluna in COLUNAS:
        op.drop_column("leads", coluna)

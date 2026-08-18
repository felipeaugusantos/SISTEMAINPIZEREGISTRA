"""Add SLA and protocol control to commercial proposals.

Revision ID: aab44s9l7v41
Revises: aaa33r8k6u30
"""

import sqlalchemy as sa
from alembic import op

revision = "aab44s9l7v41"
down_revision = "aaa33r8k6u30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("propostas_comerciais", sa.Column("pagamento_status", sa.String(20), nullable=False, server_default="pendente"))
    op.add_column("propostas_comerciais", sa.Column("pagamento_confirmado_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("propostas_comerciais", sa.Column("sla_inicio_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("propostas_comerciais", sa.Column("sla_prazo_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("propostas_comerciais", sa.Column("sla_status", sa.String(30), nullable=False, server_default="aguardando_aceite"))
    op.add_column("propostas_comerciais", sa.Column("responsavel_protocolo_id", sa.BigInteger(), nullable=True))
    op.add_column("propostas_comerciais", sa.Column("protocolo_numero", sa.String(80), nullable=True))
    op.add_column("propostas_comerciais", sa.Column("protocolo_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("propostas_comerciais", sa.Column("protocolo_motivo_atraso", sa.Text(), nullable=True))
    op.add_column("propostas_comerciais", sa.Column("protocolo_comprovante_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_propostas_comerciais_pagamento_status", "propostas_comerciais", ["pagamento_status"])
    op.create_index("ix_propostas_comerciais_sla_status", "propostas_comerciais", ["sla_status"])
    op.create_foreign_key("fk_proposta_responsavel_protocolo", "propostas_comerciais", "usuarios_operacoes", ["responsavel_protocolo_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_proposta_comprovante_documento", "propostas_comerciais", "documentos_lead", ["protocolo_comprovante_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_proposta_comprovante_documento", "propostas_comerciais", type_="foreignkey")
    op.drop_constraint("fk_proposta_responsavel_protocolo", "propostas_comerciais", type_="foreignkey")
    op.drop_index("ix_propostas_comerciais_sla_status", table_name="propostas_comerciais")
    op.drop_index("ix_propostas_comerciais_pagamento_status", table_name="propostas_comerciais")
    for column in ("protocolo_comprovante_id", "protocolo_motivo_atraso", "protocolo_em", "protocolo_numero", "responsavel_protocolo_id", "sla_status", "sla_prazo_em", "sla_inicio_em", "pagamento_confirmado_em", "pagamento_status"):
        op.drop_column("propostas_comerciais", column)

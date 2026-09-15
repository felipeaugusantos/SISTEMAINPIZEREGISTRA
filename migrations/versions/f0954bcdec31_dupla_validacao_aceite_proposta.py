"""dupla validacao no aceite de proposta

Revision ID: f0954bcdec31
Revises: 4f3fdabad4d3

Orientacao juridica (15/09/2026): aceite via SaaS pode ser formalizado por
clickwrap com dupla validacao, sem precisar de assinatura ICP-Brasil nem de
provedor externo. Campos novos para o codigo de confirmacao por e-mail
(PropostaComercial, transitorios) e para a evidencia do segundo fator
(AssinaturaPropostaComercial, permanente).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0954bcdec31"
down_revision: str | None = "4f3fdabad4d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("propostas_comerciais", sa.Column("codigo_confirmacao_hash", sa.String(64), nullable=True))
    op.add_column(
        "propostas_comerciais", sa.Column("codigo_confirmacao_expira_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "propostas_comerciais",
        sa.Column("codigo_confirmacao_tentativas", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "propostas_comerciais", sa.Column("codigo_confirmacao_enviado_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "assinaturas_propostas_comerciais", sa.Column("segundo_fator_canal", sa.String(20), nullable=True)
    )
    op.add_column(
        "assinaturas_propostas_comerciais",
        sa.Column("segundo_fator_confirmado_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assinaturas_propostas_comerciais", "segundo_fator_confirmado_em")
    op.drop_column("assinaturas_propostas_comerciais", "segundo_fator_canal")
    op.drop_column("propostas_comerciais", "codigo_confirmacao_enviado_em")
    op.drop_column("propostas_comerciais", "codigo_confirmacao_tentativas")
    op.drop_column("propostas_comerciais", "codigo_confirmacao_expira_em")
    op.drop_column("propostas_comerciais", "codigo_confirmacao_hash")

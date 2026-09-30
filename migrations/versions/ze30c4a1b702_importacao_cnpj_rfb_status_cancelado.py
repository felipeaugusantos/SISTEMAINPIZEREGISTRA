"""Permite o status "cancelado" nas importações do cache nacional (CNPJ/RFB).

Pedido do usuário (30/09/2026): botão "Parar importação" no Radar. A
constraint só aceitava executando/concluido/erro -- gravar "cancelado"
falhava com erro de integridade (revisão do Codex no PR #161).

Revision ID: ze30c4a1b702
Revises: zd81a2b3c495
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ze30c4a1b702"
down_revision: str | None = "zd81a2b3c495"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_importacoes_cnpj_rfb_status_valido", "importacoes_cnpj_rfb", type_="check")
    op.create_check_constraint(
        "ck_importacoes_cnpj_rfb_status_valido",
        "importacoes_cnpj_rfb",
        "status IN ('executando', 'concluido', 'erro', 'cancelado')",
    )


def downgrade() -> None:
    op.execute("UPDATE importacoes_cnpj_rfb SET status = 'erro' WHERE status = 'cancelado'")
    op.drop_constraint("ck_importacoes_cnpj_rfb_status_valido", "importacoes_cnpj_rfb", type_="check")
    op.create_check_constraint(
        "ck_importacoes_cnpj_rfb_status_valido",
        "importacoes_cnpj_rfb",
        "status IN ('executando', 'concluido', 'erro')",
    )

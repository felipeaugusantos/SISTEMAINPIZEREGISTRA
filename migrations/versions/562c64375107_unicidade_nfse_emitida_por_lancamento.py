"""unicidade de NFS-e emitida por lancamento (achado critico auditoria financeira, 15/09/2026)

Revision ID: 562c64375107
Revises: jw05g6h7i962

Achado: emitir_nfse (app/api/nfse.py) nao verificava se ja existia uma
NotaFiscalServico com status="emitida" para o mesmo lancamento_id antes de
chamar o adaptador -- duplo clique ou retry apos timeout emite duas notas
fiscais reais para o mesmo lancamento. Indice unico parcial (so quando
status='emitida') e a ultima linha de defesa no banco, complementando a
checagem de aplicacao adicionada em app/api/nfse.py -- uma nota "erro" ou
"cancelada" anterior nao bloqueia uma nova emissao para o mesmo lancamento
(reemissao apos cancelamento e um fluxo legitimo).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "562c64375107"
down_revision: str | None = "jw05g6h7i962"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ux_notas_fiscais_servico_lancamento_emitida",
        "notas_fiscais_servico",
        ["lancamento_id"],
        unique=True,
        postgresql_where=sa.text("status = 'emitida'"),
    )


def downgrade() -> None:
    op.drop_index("ux_notas_fiscais_servico_lancamento_emitida", table_name="notas_fiscais_servico")

"""Fase 6: reporte estruturado de problemas por versao

Revision ID: q53e9g7s397
Revises: p42d8f6r286

Estende problemas_versoes_sistema (Fase 3) com os campos de um reporte de
bug de verdade: etapas para reproduzir, resultado esperado x encontrado,
gravidade, e anexo opcional (nome/caminho/content-type/tamanho/hash --
mesmo padrao ja usado em app.storage/DocumentoEntregaJuridico). Versao,
organizacao e usuario ja eram identificados automaticamente desde a Fase
3 (colunas existentes versao_sistema_id/organizacao_id/usuario_id) --
nenhuma mudanca de schema necessaria para esses tres.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q53e9g7s397"
down_revision: str | None = "p42d8f6r286"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("problemas_versoes_sistema", sa.Column("etapas_reproduzir", sa.Text(), nullable=True))
    op.add_column("problemas_versoes_sistema", sa.Column("resultado_esperado", sa.Text(), nullable=True))
    op.add_column("problemas_versoes_sistema", sa.Column("resultado_encontrado", sa.Text(), nullable=True))
    op.add_column(
        "problemas_versoes_sistema",
        sa.Column("gravidade", sa.String(20), nullable=False, server_default="media"),
    )
    op.add_column("problemas_versoes_sistema", sa.Column("anexo_nome", sa.String(255), nullable=True))
    op.add_column("problemas_versoes_sistema", sa.Column("anexo_caminho", sa.String(500), nullable=True))
    op.add_column("problemas_versoes_sistema", sa.Column("anexo_content_type", sa.String(100), nullable=True))
    op.add_column("problemas_versoes_sistema", sa.Column("anexo_tamanho", sa.Integer(), nullable=True))
    op.add_column("problemas_versoes_sistema", sa.Column("anexo_hash", sa.String(64), nullable=True))
    op.create_index("ix_problemas_versoes_sistema_gravidade", "problemas_versoes_sistema", ["gravidade"])
    op.create_check_constraint(
        "ck_problema_versao_gravidade",
        "problemas_versoes_sistema",
        "gravidade IN ('baixa', 'media', 'alta', 'critica')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_problema_versao_gravidade", "problemas_versoes_sistema", type_="check")
    op.drop_index("ix_problemas_versoes_sistema_gravidade", table_name="problemas_versoes_sistema")
    op.drop_column("problemas_versoes_sistema", "anexo_hash")
    op.drop_column("problemas_versoes_sistema", "anexo_tamanho")
    op.drop_column("problemas_versoes_sistema", "anexo_content_type")
    op.drop_column("problemas_versoes_sistema", "anexo_caminho")
    op.drop_column("problemas_versoes_sistema", "anexo_nome")
    op.drop_column("problemas_versoes_sistema", "gravidade")
    op.drop_column("problemas_versoes_sistema", "resultado_encontrado")
    op.drop_column("problemas_versoes_sistema", "resultado_esperado")
    op.drop_column("problemas_versoes_sistema", "etapas_reproduzir")

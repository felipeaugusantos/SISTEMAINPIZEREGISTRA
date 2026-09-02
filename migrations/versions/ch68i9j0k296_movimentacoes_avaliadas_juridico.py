"""movimentacoes avaliadas juridico: marca de avaliacao para evitar backlog

Revision ID: ch68i9j0k296
Revises: bg57h8i9j185

Fase 4 da auditoria do modulo juridico (02/09/2026), achado 5.6: a consulta
de candidatos de executar_motor_organizacao usa LIMIT 2000 sem cursor,
excluindo apenas movimentacoes que ja tem PrazoJuridico. Despachos nao
mapeados (_classificar_despacho retorna None), publicacoes ja duplicadas e
movimentacoes anteriores a um despacho terminal nunca geram PrazoJuridico --
entao ficavam para sempre no pool de candidatos, ocupando vaga do LIMIT 2000
e impedindo movimentacoes mais antigas e realmente acionaveis de serem
avaliadas em organizacoes com mais de 2000 pendencias nao-classificaveis.

Esta migration cria a tabela que registra, por organizacao, que uma
movimentacao ja foi avaliada e nao gerou prazo -- ela passa a ser excluida da
consulta assim como as que ja tem PrazoJuridico. Tabela por organizacao (nao
global): Movimentacao e compartilhada entre organizacoes que monitoram o
mesmo processo, entao a mesma movimentacao pode estar avaliada para uma
organizacao e pendente para outra.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ch68i9j0k296"
down_revision: str | None = "bg57h8i9j185"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"


def upgrade() -> None:
    op.create_table(
        "movimentacoes_avaliadas_juridico",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organizacao_id", sa.BigInteger(), nullable=False),
        sa.Column("movimentacao_id", sa.BigInteger(), nullable=False),
        sa.Column("motivo", sa.String(40), nullable=False),
        sa.Column("avaliado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organizacao_id"], ["organizacoes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["movimentacao_id"], ["movimentacoes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organizacao_id",
            "movimentacao_id",
            name="uq_movimentacao_avaliada_juridico",
        ),
    )
    op.create_index(
        "ix_movimentacoes_avaliadas_juridico_organizacao_id",
        "movimentacoes_avaliadas_juridico",
        ["organizacao_id"],
    )
    op.create_index(
        "ix_movimentacoes_avaliadas_juridico_movimentacao_id",
        "movimentacoes_avaliadas_juridico",
        ["movimentacao_id"],
    )

    op.execute('ALTER TABLE "movimentacoes_avaliadas_juridico" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "movimentacoes_avaliadas_juridico" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY tenant_isolation ON "movimentacoes_avaliadas_juridico" '
        f"USING ({SUPER} OR organizacao_id = {TENANT}) "
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "movimentacoes_avaliadas_juridico" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_table("movimentacoes_avaliadas_juridico")

"""backfill: alinha status<->fase e preenche próxima ação das oportunidades abertas

Revision ID: zs55n0r6l842
Revises: zr44m9q5k731

Backfill dos registros atuais para as regras novas:
- Status alinhado à fase do funil (só avança status ainda não-terminal; nunca
  altera convertido/descartado). A fase não é movida aqui para não gerar
  histórico sintético — a sincronização vale daqui pra frente.
- Oportunidades abertas (status != convertido/descartado) sem próxima ação
  recebem um prazo padrão de 3 dias, para atender a exigência de próxima ação.
Responsável não é fabricado: leads sem responsável seguem aparecendo no card
"Sem responsável" para distribuição manual.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zs55n0r6l842"
down_revision: str | None = "zr44m9q5k731"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) status <- fase (apenas avança status de progressão; terminais intactos)
    op.execute(
        "UPDATE leads SET status='qualificado' "
        "WHERE arquivado_em IS NULL AND fase='relatorio_enviado' "
        "AND status IN ('novo','em_contato')"
    )
    op.execute(
        "UPDATE leads SET status='proposta_enviada' "
        "WHERE arquivado_em IS NULL AND fase='proposta_enviada' "
        "AND status IN ('novo','em_contato','qualificado')"
    )
    op.execute(
        "UPDATE leads SET status='convertido' "
        "WHERE arquivado_em IS NULL "
        "AND fase IN ('proposta_aceita','pagamento_realizado','protocolo_inpi','processo_inpi') "
        "AND status IN ('novo','em_contato','qualificado','proposta_enviada')"
    )
    # 2) próxima ação padrão para oportunidades abertas sem uma definida
    op.execute(
        "UPDATE leads SET proxima_acao_em = now() + interval '3 days' "
        "WHERE arquivado_em IS NULL AND proxima_acao_em IS NULL "
        "AND status NOT IN ('convertido','descartado')"
    )


def downgrade() -> None:
    # Backfill de dados: não há estado anterior a restaurar com segurança.
    pass

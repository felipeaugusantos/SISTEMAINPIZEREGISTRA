"""importa atendimentos antigos dos leads para a linha do tempo do CRM

Revision ID: zb28c1f7a064
Revises: za27b0f6d4e53
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zb28c1f7a064"
down_revision: str | None = "za27b0f6d4e53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO contatos_lead (
            organizacao_id, lead_id, empresa_id, pesquisa_id,
            operador_id, operador_nome, canal, resultado, observacao, criado_em
        )
        SELECT
            lead.organizacao_id,
            lead.id,
            lead.empresa_id,
            pesquisa.id,
            lead.responsavel_id,
            usuario.nome,
            'outro',
            'Atendimento importado do histórico de Leads',
            lead.notas,
            COALESCE(lead.ultimo_contato_em, lead.atualizado_em, lead.criado_em, now())
        FROM leads AS lead
        LEFT JOIN usuarios_operacoes AS usuario ON usuario.id = lead.responsavel_id
        LEFT JOIN LATERAL (
            SELECT item.id
            FROM pesquisas_marca AS item
            WHERE item.lead_id = lead.id
              AND item.organizacao_id = lead.organizacao_id
            ORDER BY item.criado_em DESC, item.id DESC
            LIMIT 1
        ) AS pesquisa ON true
        WHERE (
            lead.ultimo_contato_em IS NOT NULL
            OR NULLIF(btrim(lead.notas), '') IS NOT NULL
        )
          AND NOT EXISTS (
              SELECT 1 FROM contatos_lead AS contato WHERE contato.lead_id = lead.id
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM contatos_lead
        WHERE resultado = 'Atendimento importado do histórico de Leads'
        """
    )

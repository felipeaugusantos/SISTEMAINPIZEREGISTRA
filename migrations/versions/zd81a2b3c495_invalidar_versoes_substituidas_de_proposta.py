"""Invalida versões de proposta já substituídas por uma versão mais nova.

Achado 18.1 da auditoria fina de Propostas (29/09/2026): criar uma nova
versão não invalidava a anterior -- ela continuava "rascunho/enviada/
visualizada", com o link público ativo, e o cliente podia aceitar a versão
antiga (preço antigo) ou as duas, gerando duas contratações. O código novo
(criar_nova_versao_proposta) cancela a anterior no momento da criação; esta
migration aplica a mesma regra às versões que já estavam nessa situação.

Só dados: cancela a versão que tem outra mais nova com o mesmo número na
mesma organização e ainda não foi aceita, registra o motivo em
dados.cancelamento e tira de circulação o link público e o código de
confirmação. Versões ACEITAS nunca são tocadas.

Revision ID: zd81a2b3c495
Revises: zc70f9a2b184
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zd81a2b3c495"
down_revision: str | None = "zc70f9a2b184"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # propostas_comerciais tem RLS por organização; a correção vale para todas.
    op.execute("SELECT set_config('app.superadmin', 'true', true)")
    op.execute(
        """
        UPDATE propostas_comerciais AS antiga
        SET status = 'cancelada',
            dados = (
                COALESCE(antiga.dados::jsonb, '{}'::jsonb)
                || jsonb_build_object(
                    'cancelamento',
                    jsonb_build_object(
                        'motivo', 'Substituída pela versão ' || mais_nova.versao,
                        'em', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                        'por', 'migração (achado 18.1)',
                        'substituida_por_id', mais_nova.id
                    )
                )
            )::json,
            public_token_hash = NULL,
            public_token_expira_em = NULL,
            codigo_confirmacao_hash = NULL,
            codigo_confirmacao_expira_em = NULL
        FROM (
            SELECT DISTINCT ON (organizacao_id, numero) id, organizacao_id, numero, versao
            FROM propostas_comerciais
            ORDER BY organizacao_id, numero, versao DESC
        ) AS mais_nova
        WHERE antiga.organizacao_id = mais_nova.organizacao_id
          AND antiga.numero = mais_nova.numero
          AND antiga.versao < mais_nova.versao
          AND antiga.status IN ('rascunho', 'enviada', 'visualizada')
        """
    )


def downgrade() -> None:
    # Irreversível de propósito: reabrir versões substituídas recriaria o
    # risco de aceite duplo. O motivo fica registrado em dados.cancelamento.
    pass

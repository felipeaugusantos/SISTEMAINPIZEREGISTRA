"""Invalida versões de proposta já substituídas por uma versão mais nova.

Achado 18.1 da auditoria fina de Propostas (29/09/2026): criar uma nova
versão não invalidava a anterior -- ela continuava "rascunho/enviada/
visualizada", com o link público ativo, e o cliente podia aceitar a versão
antiga (preço antigo) ou as duas, gerando duas contratações. O código novo
(criar_nova_versao_proposta) cancela a anterior no momento da criação; esta
migration aplica a mesma regra às versões que já estavam nessa situação.

Só dados: cancela a versão ainda não aceita (rascunho/enviada/visualizada)
quando (a) existe outra mais nova com o mesmo número na mesma organização,
ou (b) a série já tem uma versão ACEITA -- revisão do Codex no PR #147: com
o código antigo, a v1 aceita seguida de uma v2 enviada deixava a v2 (a mais
recente) aceitável, gerando segunda contratação. Registra o motivo em
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
        WITH series AS (
            SELECT organizacao_id,
                   numero,
                   max(versao) AS versao_mais_nova,
                   max(versao) FILTER (WHERE status = 'aceita') AS versao_aceita
            FROM propostas_comerciais
            GROUP BY organizacao_id, numero
        )
        UPDATE propostas_comerciais AS alvo
        SET status = 'cancelada',
            dados = (
                COALESCE(alvo.dados::jsonb, '{}'::jsonb)
                || jsonb_build_object(
                    'cancelamento',
                    jsonb_build_object(
                        'motivo',
                        CASE
                            WHEN series.versao_aceita IS NOT NULL
                                THEN 'Série já possui a versão ' || series.versao_aceita || ' aceita'
                            ELSE 'Substituída pela versão ' || series.versao_mais_nova
                        END,
                        'em', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                        'por', 'migração (achado 18.1)'
                    )
                )
            )::json,
            public_token_hash = NULL,
            public_token_expira_em = NULL,
            codigo_confirmacao_hash = NULL,
            codigo_confirmacao_expira_em = NULL
        FROM series
        WHERE alvo.organizacao_id = series.organizacao_id
          AND alvo.numero = series.numero
          AND alvo.status IN ('rascunho', 'enviada', 'visualizada')
          AND (alvo.versao < series.versao_mais_nova OR series.versao_aceita IS NOT NULL)
        """
    )


def downgrade() -> None:
    # Irreversível de propósito: reabrir versões substituídas recriaria o
    # risco de aceite duplo. O motivo fica registrado em dados.cancelamento.
    pass

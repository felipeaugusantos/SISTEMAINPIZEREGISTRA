"""assinatura unica por proposta+versao

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8

Achado médio da Fase 8 (auditoria jurídica, 15/09/2026): nada impedia duas
linhas de AssinaturaPropostaComercial para a mesma (proposta_id, versao) --
os três canais de aceite (webhook Clicksign, link público/portal, aceite
manual) seguem o mesmo padrão "check aceito_em is None, depois insere",
sem atomicidade entre o SELECT e o INSERT. Duas entregas quase simultâneas
do mesmo webhook (o Clicksign reenvia em retry) ou um duplo clique no link
de aceite passavam as duas pelo "is None" e criavam duas linhas de
evidência de assinatura pra mesma versão da proposta.

Mesmo padrão já usado em uq_contratacao_servico_proposta (migration
ej80k1l2m418, Fase 4): unique constraint como proteção de última linha,
capturada com begin_nested()/IntegrityError no código (ver PR #48, Fase 7,
que resolveu o mesmo tipo de corrida em criar_contratacao_automatica_proposta).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Achado de produção (memória do projeto: corrupção de índice único em
    # dados que já tinham duplicata) -- o bug que esta migration corrige já
    # está em produção há tempo, então pode já existir mais de uma linha
    # pra alguma (proposta_id, versao). Remove as duplicatas (mantém a mais
    # antiga, que é a evidência original) antes de criar a constraint, pra
    # não quebrar o deploy nem deixar o índice inconsistente.
    op.execute(
        """
        DELETE FROM assinaturas_propostas_comerciais a
        USING assinaturas_propostas_comerciais b
        WHERE a.proposta_id = b.proposta_id
          AND a.versao = b.versao
          AND a.id > b.id
        """
    )
    op.create_unique_constraint(
        "uq_assinatura_proposta_versao", "assinaturas_propostas_comerciais", ["proposta_id", "versao"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_assinatura_proposta_versao", "assinaturas_propostas_comerciais", type_="unique")

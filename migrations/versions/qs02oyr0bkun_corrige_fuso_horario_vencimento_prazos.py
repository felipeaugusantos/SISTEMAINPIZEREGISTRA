"""Corrige fuso horario de vencimento_em em prazos_juridicos (achado JUR-1).

Achado JUR-1 da auditoria (04/09/2026): calcular_vencimento (app/api/juridico.py)
rotulava 23:59:59 de uma data civil brasileira diretamente como UTC, quando
deveria ser 23:59:59 em America/Sao_Paulo (= 02:59:59 UTC do dia seguinte,
ja que o Brasil nao tem mais horario de verao desde 2019 -- offset fixo de
-3h o ano inteiro, entao a correcao e sempre "+3 horas", sem excecao
sazonal). Isso fazia prazos aparecerem como vencidos ate 3 horas antes da
meia-noite real em Brasilia.

A data civil (`vencimento_em.date()`) dos registros ja existentes esta
correta -- so a hora precisa ser corrigida. Como o valor antigo era
`<data> 23:59:59 UTC` e o correto e `<data> 23:59:59 America/Sao_Paulo`
(= `<data+1> 02:59:59 UTC`), a correcao e uma soma direta de 3 horas.
"""

from alembic import op

revision = "qs02oyr0bkun"
down_revision = "prjnxiq9ajtm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE prazos_juridicos SET vencimento_em = vencimento_em + interval '3 hours'")


def downgrade() -> None:
    op.execute("UPDATE prazos_juridicos SET vencimento_em = vencimento_em - interval '3 hours'")

"""proposta numero versao: unicidade passa a incluir a versao

Revision ID: fk91l2m3n529
Revises: ej80k1l2m418

Fase 5 do plano proposta-financeiro (03/09/2026), achado 9: nova versao de
proposta gerava um numero-base novo (PROP-{ano}-{id:06d}), mesmo existindo o
campo `versao` para isso -- o numero nao refletia uma base compartilhada
entre as versoes de uma mesma proposta. A partir de agora, uma nova versao
mantem o mesmo `numero` da anterior e so incrementa `versao`.

Isso exige trocar a constraint de unicidade de (organizacao_id, numero) para
(organizacao_id, numero, versao) -- do contrario, a segunda versao de uma
proposta violaria a unicidade antiga ao reusar o mesmo numero. Propostas ja
existentes (cada versao com numero proprio, do esquema antigo) continuam
validas sob a nova constraint sem qualquer alteracao de dados: o esquema
antigo tambem satisfaz (org, numero, versao) unico, so nao reaproveita o
numero-base -- nao ha reprocessamento retroativo.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "fk91l2m3n529"
down_revision: str | None = "ej80k1l2m418"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_proposta_org_numero", "propostas_comerciais", type_="unique")
    op.create_unique_constraint(
        "uq_proposta_org_numero_versao", "propostas_comerciais", ["organizacao_id", "numero", "versao"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_proposta_org_numero_versao", "propostas_comerciais", type_="unique")
    op.create_unique_constraint("uq_proposta_org_numero", "propostas_comerciais", ["organizacao_id", "numero"])

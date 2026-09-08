"""radar de prospeccao: nova classificacao de triagem "possui_outra_marca_registrada"

Revision ID: lf97a2h8t519
Revises: ke86z1g7s408

Achado do usuário (08/09/2026): o enum ClassificacaoTriagemProspect ganhou o
valor POSSUI_OUTRA_MARCA_REGISTRADA (app/prospeccao_triagem.py, checagem
ampla de titularidade -- base local + busca ao vivo por CNPJ no INPI), mas
as constraints que validam esse campo no banco (criadas em
p46dwo6js6qi_radar_prospeccao_triagem_marca.py, ampliadas em
at75n1u9j286_triagem_marca_ja_e_titular.py para incluir "ja_e_titular")
nunca foram atualizadas com o novo valor -- bug pré-existente, mesmo padrão
do ck_leads_origem_valida corrigido em ke86z1g7s408. Confirmado em produção:
o job prospeccao.triar_marca_prospect falhou com CheckViolationError em
ck_prospect_triagens_classificacao_valida ao reprocessar triagens antigas
com a busca ao vivo já ativa.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "lf97a2h8t519"
down_revision: str | None = "ke86z1g7s408"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLASSIFICACOES_TRIAGEM_ANTIGAS = (
    "nao_localizado",
    "resultado_semelhante",
    "resultado_relevante_localizado",
    "inconclusivo",
    "analise_humana_necessaria",
    "ja_e_titular",
)
CLASSIFICACOES_TRIAGEM_NOVAS = (*CLASSIFICACOES_TRIAGEM_ANTIGAS, "possui_outra_marca_registrada")


def upgrade() -> None:
    op.drop_constraint("ck_prospects_triagem_marca_status_valido", "prospects", type_="check")
    op.create_check_constraint(
        "ck_prospects_triagem_marca_status_valido",
        "prospects",
        "triagem_marca_status IS NULL OR triagem_marca_status IN ("
        + ", ".join(f"'{v}'" for v in CLASSIFICACOES_TRIAGEM_NOVAS)
        + ")",
    )
    op.drop_constraint("ck_prospect_triagens_classificacao_valida", "prospect_triagens", type_="check")
    op.create_check_constraint(
        "ck_prospect_triagens_classificacao_valida",
        "prospect_triagens",
        "classificacao IN (" + ", ".join(f"'{v}'" for v in CLASSIFICACOES_TRIAGEM_NOVAS) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_prospect_triagens_classificacao_valida", "prospect_triagens", type_="check")
    op.create_check_constraint(
        "ck_prospect_triagens_classificacao_valida",
        "prospect_triagens",
        "classificacao IN (" + ", ".join(f"'{v}'" for v in CLASSIFICACOES_TRIAGEM_ANTIGAS) + ")",
    )
    op.drop_constraint("ck_prospects_triagem_marca_status_valido", "prospects", type_="check")
    op.create_check_constraint(
        "ck_prospects_triagem_marca_status_valido",
        "prospects",
        "triagem_marca_status IS NULL OR triagem_marca_status IN ("
        + ", ".join(f"'{v}'" for v in CLASSIFICACOES_TRIAGEM_ANTIGAS)
        + ")",
    )

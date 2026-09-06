"""radar de prospeccao: nova classificacao de triagem "ja_e_titular"

Revision ID: at75n1u9j286
Revises: zw08g6y0j519

Achado da sessao de 05/09/2026: alem das 5 classificacoes indicativas
existentes (nunca "disponivel"/"livre"), a triagem agora identifica um sinal
factual -- o proprio prospect ja consta como titular de um processo com nome
identico ao pesquisado (ver app/prospeccao_triagem.py::_prospect_e_titular).
Isso nao e parecer de registrabilidade, e sim uma checagem de banco de dados
("essa empresa ja tem esse registro"), que habilita descarte rapido na tela
("ja_possui_marca_registrada" em app/api/prospeccao.py::MOTIVOS_DESCARTE_PROSPECT).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "at75n1u9j286"
down_revision: str | None = "zw08g6y0j519"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLASSIFICACOES_TRIAGEM_ANTIGAS = (
    "nao_localizado",
    "resultado_semelhante",
    "resultado_relevante_localizado",
    "inconclusivo",
    "analise_humana_necessaria",
)
CLASSIFICACOES_TRIAGEM_NOVAS = (*CLASSIFICACOES_TRIAGEM_ANTIGAS, "ja_e_titular")


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

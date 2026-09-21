"""redige dados pessoais ja persistidos do payload clicksign

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9

Achado médio da Fase 8 (auditoria jurídica, revisão Codex de 21/09/2026):
a redação do payload bruto do webhook Clicksign (PR #98,
`_redigir_payload_webhook`) só se aplica a eventos processados a partir do
deploy. Envelopes já concluídos guardavam nome, e-mail, CPF/CNPJ etc. em
`PropostaComercial.dados["clicksign"]["ultimo_evento"]` sem redação e
talvez nunca recebam outro evento pra sobrescrever esse valor. Esta
migration de dados varre as linhas já existentes e redige os mesmos
campos sensíveis, em qualquer profundidade.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CAMPOS_SENSIVEIS_WEBHOOK_CLICKSIGN = {
    "name",
    "nome",
    "full_name",
    "email",
    "e-mail",
    "phone",
    "phone_number",
    "telefone",
    "documentation",
    "cpf",
    "cnpj",
    "birthday",
    "nascimento",
    "ip",
    "ip_address",
    "geolocation",
    "geo",
    "address",
    "endereco",
    "selfie",
}


def _redigir_payload_webhook(valor: object) -> object:
    if isinstance(valor, dict):
        return {
            chave: "[redigido]" if chave.lower() in _CAMPOS_SENSIVEIS_WEBHOOK_CLICKSIGN else _redigir_payload_webhook(sub)
            for chave, sub in valor.items()
        }
    if isinstance(valor, list):
        return [_redigir_payload_webhook(item) for item in valor]
    return valor


def upgrade() -> None:
    conexao = op.get_bind()
    linhas = conexao.execute(
        sa.text(
            """
            SELECT id, dados FROM propostas_comerciais
            WHERE dados #> '{clicksign,ultimo_evento}' IS NOT NULL
            """
        )
    ).fetchall()
    for linha in linhas:
        dados = dict(linha.dados or {})
        clicksign = dict(dados.get("clicksign") or {})
        clicksign["ultimo_evento"] = _redigir_payload_webhook(clicksign.get("ultimo_evento"))
        dados["clicksign"] = clicksign
        conexao.execute(
            sa.text("UPDATE propostas_comerciais SET dados = CAST(:dados AS json) WHERE id = :id"),
            {"dados": json.dumps(dados), "id": linha.id},
        )


def downgrade() -> None:
    # Redação de dados pessoais é irreversível por design -- não há como
    # reconstruir os valores originais a partir do valor "[redigido]".
    pass

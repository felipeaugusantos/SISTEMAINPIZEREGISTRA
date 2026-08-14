"""preenche valores oficiais das retribuições de marca (Tabela INPI 2025)

Revision ID: zn00i5m1g397
Revises: zm99h4l0f286

Fonte: Tabela de Retribuições dos Serviços prestados pelo INPI —
Portaria GM/MDIC nº 110/2025 e Portaria INPI/PR nº 10/2025 (vigência 20/09/2025).
Valores em R$: (valor_normal / valor_reduzido). Reduzido = ME/EPP/pessoa física
e demais beneficiários do art. 6º da Lei 9.279/96 conforme portaria.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zn00i5m1g397"
down_revision: str | None = "zm99h4l0f286"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FONTE = "Tabela INPI — Port. MDIC 110/2025 e INPI 10/2025 (vig. 20/09/2025)."

# (servico, codigo, valor_normal, valor_reduzido, confirmado, observacoes)
VALORES = [
    ("deposito", "389", "360.00", "180.00", True,
     f"Especificação pré-aprovada, por classe. Multiclasse/classe adicional: 880,00 / 440,00. {FONTE}"),
    ("exigencia_exame", "338", "180.00", "90.00", True,
     f"Exigência decorrente de exame formal em pedido de registro. {FONTE}"),
    ("oposicao", "332", "520.00", "260.00", True, f"Por classe. {FONTE}"),
    ("manifestacao_oposicao", "339", "180.00", "90.00", True, f"Manifestação. {FONTE}"),
    ("concessao_1decenio", "372", "750.00", "375.00", True,
     f"Prazo ordinário, por classe. Prazo extraordinário (cód. 373): 1.120,00 / 560,00. {FONTE}"),
    ("prorrogacao", "374", "1000.00", "500.00", True, f"Prazo ordinário, por classe. {FONTE}"),
    ("prorrogacao_extraordinaria", "375", "2000.00", "1000.00", True,
     f"Prazo extraordinário (até 6 meses após), por classe. {FONTE}"),
    ("recurso", "3000", "700.00", "350.00", True,
     f"Recurso contra indeferimento do pedido, por classe. Outros recursos (cód. 333): 700,00 / 350,00. {FONTE}"),
    ("nulidade", "336", "850.00", "425.00", True, f"PAN, por classe. {FONTE}"),
    ("caducidade", "337", "590.00", "295.00", True, f"Por classe. {FONTE}"),
    ("segunda_via_certificado", "351", "140.00", None, True,
     f"Valor cheio; desconto/reduzido a confirmar na tabela. {FONTE}"),
    ("transferencia", "349", "170.00", None, True,
     f"Anotação de transferência de titular, 1º processo. Processo adicional: 90,00. Reduzido a confirmar. {FONTE}"),
    # alteração: extração ambígua — só o código; valores a confirmar
    ("alteracao", "348", None, None, False,
     f"Alteração de nome, sede e/ou endereço. Confira o valor na tabela oficial. {FONTE}"),
]

# Serviço novo: depósito com especificação de livre preenchimento (cód. 394).
NOVO = {
    "servico": "deposito_livre",
    "descricao": "Depósito de marca (especificação de livre preenchimento)",
    "grupo": "marca",
    "codigo": "394",
    "valor_normal": "420.00",
    "valor_reduzido": "210.00",
    "fase_sugerida": "protocolo_inpi",
    "confirmado": True,
    "ativo": True,
    "ordem": 1,
    "observacoes": f"Por classe. Alternativa ao cód. 389 quando a especificação é digitada livremente. {FONTE}",
}


def upgrade() -> None:
    bind = op.get_bind()
    upd = sa.text(
        "UPDATE retribuicoes_inpi SET codigo=:codigo, valor_normal=:vn, "
        "valor_reduzido=:vr, confirmado=:conf, observacoes=:obs, atualizado_em=now() "
        "WHERE servico=:servico"
    )
    for servico, codigo, vn, vr, conf, obs in VALORES:
        bind.execute(
            upd,
            {"codigo": codigo, "vn": vn, "vr": vr, "conf": conf, "obs": obs, "servico": servico},
        )

    existe = bind.execute(
        sa.text("SELECT 1 FROM retribuicoes_inpi WHERE servico=:s"), {"s": NOVO["servico"]}
    ).first()
    if not existe:
        bind.execute(
            sa.text(
                "INSERT INTO retribuicoes_inpi "
                "(servico, descricao, grupo, codigo, valor_normal, valor_reduzido, "
                " fase_sugerida, confirmado, ativo, ordem, observacoes, atualizado_em) "
                "VALUES (:servico, :descricao, :grupo, :codigo, :valor_normal, :valor_reduzido, "
                " :fase_sugerida, :confirmado, :ativo, :ordem, :observacoes, now())"
            ),
            NOVO,
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM retribuicoes_inpi WHERE servico='deposito_livre'"))
    servicos = [v[0] for v in VALORES]
    bind.execute(
        sa.text(
            "UPDATE retribuicoes_inpi SET codigo=NULL, valor_normal=NULL, valor_reduzido=NULL, "
            "confirmado=false, observacoes=NULL WHERE servico = ANY(:servicos)"
        ),
        {"servicos": servicos},
    )

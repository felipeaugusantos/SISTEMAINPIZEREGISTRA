"""atualiza retribuições de marca para a tabela oficial vigente (20/12/2025)

Fonte: TABELA DE RETRIBUIÇÕES DOS SERVIÇOS PRESTADOS PELO INPI — Serviços
relativos a marcas. Portaria GM/MDIC nº 110/2025, Portaria INPI/PR nº 10/2025
e apostila 31/10/2025.

Revision ID: zr55m9n2k731
Revises: ab63d0e8f924
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zr55m9n2k731"
down_revision: str | None = "ab63d0e8f924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOTA = (
    "Portaria GM/MDIC 110/2025; INPI/PR 10/2025; apostila 31/10/2025 "
    "(tabela vigente 20/12/2025)"
)

# codigo -> (valor_normal, valor_reduzido) conforme tabela oficial de marcas.
VALORES_OFICIAIS = {
    "389": (880.00, 440.00),  # Pedido (especificação pré-aprovada) - por classe
    "394": (1720.00, 860.00),  # Pedido (livre preenchimento) - por classe
    "338": (180.00, 90.00),  # Cumprimento de exigência (exame formal)
    "332": (520.00, 260.00),  # Oposição - por classe
    "339": (180.00, 90.00),  # Manifestação
    "372": (0.00, 0.00),  # Concessão 1º decênio (prazo ordinário) - agora gratuito
    "374": (1000.00, 500.00),  # Prorrogação (prazo ordinário) - por classe
    "375": (2000.00, 1000.00),  # Prorrogação (prazo extraordinário) - por classe
    "3000": (700.00, 350.00),  # Recurso contra indeferimento - por classe
    "336": (850.00, 425.00),  # Nulidade administrativa - por classe
    "337": (590.00, 295.00),  # Caducidade - por classe
    "349": (170.00, None),  # Anotação de transferência de titular (1º processo)
    "348": (50.00, None),  # Alteração de nome, sede e/ou endereço
    "351": (140.00, None),  # Expedição de 2ª via de certificado
}

# Valores anteriores (para downgrade), somente onde houve mudança real.
VALORES_ANTERIORES = {
    "389": (360.00, 180.00),
    "394": (420.00, 210.00),
    "372": (750.00, 375.00),
    "348": (None, None),
}


def _aplicar(valores: dict[str, tuple[float | None, float | None]]) -> None:
    stmt = sa.text(
        "UPDATE retribuicoes_inpi SET valor_normal = :normal, valor_reduzido = :reduzido, "
        "confirmado = true, atualizado_em = now() WHERE codigo = :codigo"
    )
    for codigo, (normal, reduzido) in valores.items():
        op.execute(stmt.bindparams(normal=normal, reduzido=reduzido, codigo=codigo))


def upgrade() -> None:
    _aplicar(VALORES_OFICIAIS)
    op.execute(
        sa.text(
            "UPDATE retribuicoes_inpi SET observacoes = :nota "
            "WHERE observacoes IS NULL OR observacoes = ''"
        ).bindparams(nota=NOTA)
    )


def downgrade() -> None:
    _aplicar(VALORES_ANTERIORES)

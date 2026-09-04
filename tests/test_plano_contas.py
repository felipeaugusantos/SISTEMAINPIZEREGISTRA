from datetime import date
from decimal import Decimal

from app.plano_contas import CONTAS_PADRAO, GRUPOS_DRE, montar_dre

# --- Achado FASE7-13/14 da auditoria (04/09/2026): plano de contas gerencial
# e DRE -- app.plano_contas.montar_dre é pura, sem depender de banco. ---


def test_grupos_dre_cobrem_todas_as_contas_padrao() -> None:
    grupos_usados = {grupo for _codigo, _nome, _natureza, grupo in CONTAS_PADRAO}
    assert grupos_usados.issubset(set(GRUPOS_DRE))


def test_dre_so_com_receita_bruta() -> None:
    resultado = montar_dre(
        competencia_de=date(2026, 1, 1),
        competencia_ate=date(2026, 1, 31),
        totais_por_grupo={"receita_bruta": (Decimal("10000"), Decimal("0"))},
    )
    assert resultado.receita_bruta == Decimal("10000")
    assert resultado.receita_liquida == Decimal("10000")
    assert resultado.lucro_bruto == Decimal("10000")
    assert resultado.resultado_operacional == Decimal("10000")
    assert resultado.resultado_liquido == Decimal("10000")


def test_dre_completo_com_todos_os_grupos() -> None:
    resultado = montar_dre(
        competencia_de=date(2026, 1, 1),
        competencia_ate=date(2026, 1, 31),
        totais_por_grupo={
            "receita_bruta": (Decimal("10000"), Decimal("0")),
            "deducoes_receita": (Decimal("0"), Decimal("500")),
            "custos_diretos": (Decimal("0"), Decimal("2000")),
            "despesas_operacionais": (Decimal("0"), Decimal("3000")),
            "despesas_administrativas": (Decimal("0"), Decimal("1000")),
            "resultado_financeiro": (Decimal("200"), Decimal("100")),
        },
    )
    assert resultado.receita_bruta == Decimal("10000")
    assert resultado.receita_liquida == Decimal("9500")
    assert resultado.lucro_bruto == Decimal("7500")
    assert resultado.resultado_operacional == Decimal("3500")
    # resultado_operacional (3500) + resultado_financeiro (200 - 100 = 100)
    assert resultado.resultado_liquido == Decimal("3600")


def test_dre_grupo_ausente_conta_como_zero() -> None:
    resultado = montar_dre(
        competencia_de=date(2026, 1, 1), competencia_ate=date(2026, 1, 31), totais_por_grupo={}
    )
    assert resultado.receita_bruta == Decimal("0")
    assert resultado.resultado_liquido == Decimal("0")


def test_dre_registra_valor_sem_classificacao_separadamente() -> None:
    resultado = montar_dre(
        competencia_de=date(2026, 1, 1),
        competencia_ate=date(2026, 1, 31),
        totais_por_grupo={"receita_bruta": (Decimal("1000"), Decimal("0"))},
        valor_sem_classificacao=Decimal("500"),
    )
    # valor_sem_classificacao não entra no cálculo do resultado -- só é
    # reportado separadamente, para não inflar/subestimar o DRE silenciosamente.
    assert resultado.receita_bruta == Decimal("1000")
    assert resultado.valor_sem_classificacao == Decimal("500")


def test_dre_prejuizo_resulta_em_saldo_negativo() -> None:
    resultado = montar_dre(
        competencia_de=date(2026, 1, 1),
        competencia_ate=date(2026, 1, 31),
        totais_por_grupo={
            "receita_bruta": (Decimal("1000"), Decimal("0")),
            "despesas_operacionais": (Decimal("0"), Decimal("5000")),
        },
    )
    assert resultado.resultado_liquido == Decimal("-4000")

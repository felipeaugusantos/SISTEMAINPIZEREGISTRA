"""Plano de contas e DRE gerencial -- FASE7 da auditoria (04/09/2026).

Achado FASE7-13/14: o financeiro tinha CategoriaFinanceira/CentroCustoFinanceiro
(dimensões analíticas soltas, sem hierarquia) mas nenhum plano de contas
contábil de verdade nem DRE. Este módulo fica deliberadamente simples --
"DRE gerencial" (regime de competência, para gestão interna), não um plano
de contas fiscal/societário completo (isso exigiria um contador definindo a
estrutura, fora do escopo de uma correção de auditoria técnica).

Cada LancamentoFinanceiro pode apontar para uma PlanoContas (conta_contabil_id,
opcional -- lançamentos antigos continuam funcionando sem classificação,
aparecem como "sem_classificacao" no DRE em vez de desaparecer silenciosamente).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# Ordem fixa de apresentação no DRE -- da receita bruta ao resultado líquido.
GRUPOS_DRE: tuple[str, ...] = (
    "receita_bruta",
    "deducoes_receita",
    "custos_diretos",
    "despesas_operacionais",
    "despesas_administrativas",
    "resultado_financeiro",
)

RUBRICA_SEM_CLASSIFICACAO = "sem_classificacao"

# (codigo, nome, natureza, grupo_dre) -- ponto de partida razoável para um
# escritório de PI; a organização pode editar/expandir depois via API.
CONTAS_PADRAO: tuple[tuple[str, str, str, str], ...] = (
    ("3.1", "Honorários de registro de marca", "receita", "receita_bruta"),
    ("3.2", "Honorários de acompanhamento/monitoramento", "receita", "receita_bruta"),
    ("3.3", "Outras receitas de serviços", "receita", "receita_bruta"),
    ("4.1", "Impostos sobre serviços (ISS/PIS/COFINS)", "despesa", "deducoes_receita"),
    ("5.1", "GRU e taxas oficiais do INPI repassadas", "despesa", "custos_diretos"),
    ("5.2", "Custos diretos de processo (terceiros, tradução, cartório)", "despesa", "custos_diretos"),
    ("6.1", "Folha e comissões", "despesa", "despesas_operacionais"),
    ("6.2", "Marketing e prospecção", "despesa", "despesas_operacionais"),
    ("6.3", "Ferramentas e assinaturas", "despesa", "despesas_operacionais"),
    ("7.1", "Administrativo e ocupação (aluguel, contabilidade)", "despesa", "despesas_administrativas"),
    ("8.1", "Juros e tarifas bancárias", "despesa", "resultado_financeiro"),
    ("8.2", "Rendimentos financeiros", "receita", "resultado_financeiro"),
)


@dataclass(frozen=True, slots=True)
class LinhaDre:
    grupo: str
    receitas: Decimal
    despesas: Decimal
    saldo: Decimal


@dataclass(frozen=True, slots=True)
class ResultadoDre:
    competencia_de: date
    competencia_ate: date
    linhas: list[LinhaDre] = field(default_factory=list)
    receita_bruta: Decimal = Decimal("0")
    receita_liquida: Decimal = Decimal("0")
    lucro_bruto: Decimal = Decimal("0")
    resultado_operacional: Decimal = Decimal("0")
    resultado_liquido: Decimal = Decimal("0")
    valor_sem_classificacao: Decimal = Decimal("0")


def montar_dre(
    *, competencia_de: date, competencia_ate: date, totais_por_grupo: dict[str, tuple[Decimal, Decimal]],
    valor_sem_classificacao: Decimal = Decimal("0"),
) -> ResultadoDre:
    """Monta o DRE a partir de totais já agregados por grupo (receitas, despesas)
    -- a agregação em si (somar LancamentoFinanceiro por conta) fica na camada
    de API, que tem acesso à sessão do banco; esta função é pura, fácil de
    testar sem banco.
    """
    linhas = []
    for grupo in GRUPOS_DRE:
        receitas, despesas = totais_por_grupo.get(grupo, (Decimal("0"), Decimal("0")))
        linhas.append(LinhaDre(grupo=grupo, receitas=receitas, despesas=despesas, saldo=receitas - despesas))

    por_grupo = {linha.grupo: linha for linha in linhas}
    receita_bruta = por_grupo["receita_bruta"].receitas - por_grupo["receita_bruta"].despesas
    receita_liquida = receita_bruta - por_grupo["deducoes_receita"].despesas + por_grupo["deducoes_receita"].receitas
    lucro_bruto = receita_liquida - por_grupo["custos_diretos"].despesas + por_grupo["custos_diretos"].receitas
    resultado_operacional = (
        lucro_bruto
        - por_grupo["despesas_operacionais"].despesas
        + por_grupo["despesas_operacionais"].receitas
        - por_grupo["despesas_administrativas"].despesas
        + por_grupo["despesas_administrativas"].receitas
    )
    resultado_liquido = resultado_operacional + por_grupo["resultado_financeiro"].saldo

    return ResultadoDre(
        competencia_de=competencia_de,
        competencia_ate=competencia_ate,
        linhas=linhas,
        receita_bruta=receita_bruta,
        receita_liquida=receita_liquida,
        lucro_bruto=lucro_bruto,
        resultado_operacional=resultado_operacional,
        resultado_liquido=resultado_liquido,
        valor_sem_classificacao=valor_sem_classificacao,
    )

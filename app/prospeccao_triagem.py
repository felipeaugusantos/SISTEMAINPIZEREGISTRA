"""Triagem de marca para Prospect -- SOMENTE indicativa, nunca definitiva
(Fase 4 do Radar de Prospecção, 03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md).

IMPORTANTE (exigência explícita do usuário): este módulo NUNCA pode afirmar
que uma marca está disponível para registro. As únicas classificações
possíveis são as 5 do enum abaixo -- "disponível"/"livre" não existe nesse
vocabulário de propósito, não é uma regra de UI que pode ser esquecida, é a
única coisa que o enum permite devolver.

Reaproveita o motor de busca já existente (app.search.buscar_marcas) e o
léxico de peso semântico (app.trademarks.lexico, via
app.search.termos_comuns_do_match) -- não duplica nenhuma infraestrutura.
"""

from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.search import OcorrenciaBusca, buscar_marcas, termos_comuns_do_match


class ClassificacaoTriagemProspect(StrEnum):
    NAO_LOCALIZADO = "nao_localizado"
    RESULTADO_SEMELHANTE = "resultado_semelhante"
    RESULTADO_RELEVANTE_LOCALIZADO = "resultado_relevante_localizado"
    INCONCLUSIVO = "inconclusivo"
    ANALISE_HUMANA_NECESSARIA = "analise_humana_necessaria"


DISCLAIMER_TRIAGEM = (
    "Esta triagem é automática e apenas indicativa (achado de possível conflito "
    "nominativo na base de marcas). Não substitui uma pesquisa formal de "
    "anterioridade nem constitui parecer sobre registrabilidade -- termos de uso "
    "comum não garantem exclusividade isolada, e a decisão final sobre o registro "
    "é sempre do INPI."
)

# Sufixos societários comuns (com e sem pontuação) removidos da razão social
# para chegar a uma proxy de "nome de marca" quando o prospect não trouxe
# nome fantasia -- ex.: "PADARIA DO JOAO LTDA" -> "PADARIA DO JOAO".
SUFIXOS_SOCIETARIOS = frozenset(
    {"LTDA", "ME", "EIRELI", "EPP", "MEI", "S/A", "SA", "SS"}
)

LIMITE_BUSCA_TRIAGEM = 50
LIMIAR_RESULTADO_RELEVANTE = 50.0
LIMIAR_RESULTADO_SEMELHANTE = 15.0


def extrair_marca_candidata(razao_social: str, nome_fantasia: str | None) -> str | None:
    """Nome fantasia é sempre a proxy melhor (é o que a empresa de fato usa
    como marca); na ausência, tenta a razão social sem os sufixos societários."""
    if nome_fantasia and nome_fantasia.strip():
        return nome_fantasia.strip()
    palavras = (razao_social or "").strip().split()
    limpas = [palavra for palavra in palavras if palavra.strip(".,").upper() not in SUFIXOS_SOCIETARIOS]
    candidata = " ".join(limpas).strip(" .,-")
    return candidata or None


def _melhor_ocorrencia_e_justificativa(
    ocorrencias: list[OcorrenciaBusca],
) -> tuple[OcorrenciaBusca, str]:
    melhor = ocorrencias[0]
    return melhor, f'"{melhor.processo.titulo}" (processo {melhor.processo.numero})'


def classificar(
    total_resultados: int, ocorrencias: list[OcorrenciaBusca], marca_candidata: str
) -> tuple[ClassificacaoTriagemProspect, str]:
    """Decide a classificação a partir do resultado já calculado por
    buscar_marcas() -- não roda nenhuma query, só interpreta."""
    if total_resultados > LIMITE_BUSCA_TRIAGEM:
        return (
            ClassificacaoTriagemProspect.ANALISE_HUMANA_NECESSARIA,
            f'A busca por "{marca_candidata}" encontrou mais de {LIMITE_BUSCA_TRIAGEM} ocorrências -- '
            "volume alto demais para uma triagem automática confiável.",
        )
    if total_resultados == 0:
        return (
            ClassificacaoTriagemProspect.NAO_LOCALIZADO,
            f'Nenhuma ocorrência encontrada na base de marcas para "{marca_candidata}".',
        )
    if not ocorrencias:
        return (
            ClassificacaoTriagemProspect.INCONCLUSIVO,
            "A busca indicou resultados, mas não foi possível analisá-los automaticamente.",
        )

    melhor, referencia = _melhor_ocorrencia_e_justificativa(ocorrencias)
    termos_comuns = termos_comuns_do_match(melhor.processo.titulo, marca_candidata)
    if termos_comuns and melhor.score.total < LIMIAR_RESULTADO_RELEVANTE:
        return (
            ClassificacaoTriagemProspect.INCONCLUSIVO,
            f"A ocorrência mais próxima ({referencia}) só coincide em termo(s) de uso comum "
            f"({', '.join(termos_comuns)}), sem elemento distintivo compartilhado -- necessária "
            "análise humana para concluir.",
        )
    if melhor.score.total >= LIMIAR_RESULTADO_RELEVANTE:
        return (
            ClassificacaoTriagemProspect.RESULTADO_RELEVANTE_LOCALIZADO,
            f"Encontrada ocorrência com forte semelhança nominativa: {referencia}.",
        )
    if melhor.score.total >= LIMIAR_RESULTADO_SEMELHANTE:
        return (
            ClassificacaoTriagemProspect.RESULTADO_SEMELHANTE,
            f"Encontrada ocorrência com semelhança parcial: {referencia}.",
        )
    return (
        ClassificacaoTriagemProspect.INCONCLUSIVO,
        f"As ocorrências encontradas (a mais próxima: {referencia}) têm semelhança fraca com o "
        "termo pesquisado -- necessária análise humana.",
    )


async def triar_marca_prospect(session: AsyncSession, razao_social: str, nome_fantasia: str | None) -> dict:
    """Roda a triagem e devolve o dict pronto para virar ProspectTriagem.
    Não grava nada -- quem chama decide persistir (ver app/worker.py)."""
    marca_candidata = extrair_marca_candidata(razao_social, nome_fantasia)
    if not marca_candidata or len(marca_candidata) < 2:
        return {
            "marca_pesquisada": (nome_fantasia or razao_social or "").strip()[:200] or "(sem nome)",
            "classificacao": ClassificacaoTriagemProspect.ANALISE_HUMANA_NECESSARIA.value,
            "justificativa": "O nome da empresa não permite compor um termo de busca significativo "
            "(sem nome fantasia e razão social só com termos societários genéricos).",
            "total_resultados": 0,
        }

    total, ocorrencias, _evidencias = await buscar_marcas(
        session, marca_candidata, tipo_pesquisa="completa", classe_nice=None, limite=LIMITE_BUSCA_TRIAGEM
    )
    classificacao, justificativa = classificar(total, ocorrencias, marca_candidata)
    return {
        "marca_pesquisada": marca_candidata[:200],
        "classificacao": classificacao.value,
        "justificativa": justificativa,
        "total_resultados": total,
    }

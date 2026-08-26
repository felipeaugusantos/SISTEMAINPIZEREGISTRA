from dataclasses import dataclass

ORDEM_RELEVANCIA = {"critica": 0, "alta": 1, "media": 2, "baixa": 3}


@dataclass(frozen=True, slots=True)
class RelevanciaOcorrencia:
    nivel: str
    rotulo: str
    justificativas: tuple[str, ...]


def classificar_relevancia(
    criterios: list[str],
    *,
    alto_renome: bool,
    afinidade_nivel: str | None,
    relevancia_situacao: str | None,
) -> RelevanciaOcorrencia:
    motivos: list[str] = []
    nome_identico = "Nome idêntico" in criterios
    expressao_completa = "Expressão completa" in criterios
    situacao_ativa = relevancia_situacao in {"alta", "ativa"}
    situacao_inativa = relevancia_situacao in {"baixa", "inativa"}

    if alto_renome:
        motivos.append("Processo associado a marca de alto renome")
    if nome_identico:
        motivos.append("Elemento nominativo idêntico")
    elif expressao_completa:
        motivos.append("Expressão completa encontrada")
    elif criterios:
        motivos.append(criterios[0])

    if afinidade_nivel == "alta":
        motivos.append("Classe idêntica ou atividade com alta afinidade")
    elif afinidade_nivel == "media":
        motivos.append("Atividade com afinidade intermediária")
    elif afinidade_nivel == "baixa":
        motivos.append("Baixa afinidade entre as atividades")

    if situacao_ativa:
        motivos.append("Processo com situação potencialmente ativa")
    elif situacao_inativa:
        motivos.append("Processo com situação inativa ou encerrada")

    if alto_renome or (nome_identico and situacao_ativa and afinidade_nivel != "baixa"):
        return RelevanciaOcorrencia("critica", "Atenção crítica", tuple(motivos))
    if (nome_identico or expressao_completa) and not situacao_inativa:
        return RelevanciaOcorrencia("alta", "Alta relevância", tuple(motivos))
    if afinidade_nivel in {"alta", "media"} and not situacao_inativa:
        return RelevanciaOcorrencia("media", "Relevância intermediária", tuple(motivos))
    return RelevanciaOcorrencia(
        "baixa",
        "Baixa relevância aparente",
        tuple(motivos or ["Aproximação nominativa sem outros agravantes identificados"]),
    )


def construir_conclusao(itens: list[object], total: int) -> tuple[str, str, str, bool]:
    """Produz somente uma triagem indicativa; nunca declara registrabilidade."""
    niveis = [getattr(item, "relevancia", "baixa") for item in itens]
    if "critica" in niveis:
        return (
            "atencao_critica",
            "Foram localizadas ocorrências de atenção crítica",
            "Há sinais nominativos e contextuais que justificam revisão humana antes de decidir.",
            True,
        )
    if "alta" in niveis:
        return (
            "atencao_elevada",
            "Foram localizadas ocorrências de alta relevância",
            "A pesquisa encontrou sinais que merecem comparação detalhada de classes e mercado.",
            True,
        )
    if "media" in niveis:
        return (
            "atencao",
            "Foram localizadas ocorrências que merecem atenção",
            "Os resultados são indicativos e devem ser interpretados no contexto da atividade.",
            True,
        )
    if total:
        return (
            "baixa_relevancia",
            "Não foi identificado conflito evidente entre os itens mais relevantes",
            "Existem ocorrências nominativas, mas sem agravantes suficientes para uma conclusão.",
            False,
        )
    return (
        "nenhuma_ocorrencia",
        "Nenhuma ocorrência foi localizada pelos critérios aplicados",
        "Isso não significa disponibilidade ou garantia de registro; outras análises podem ser necessárias.",
        False,
    )

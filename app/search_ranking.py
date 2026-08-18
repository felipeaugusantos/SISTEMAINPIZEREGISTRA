from dataclasses import asdict, dataclass

# O score de busca ordena resultados; ele não representa risco nem probabilidade.
# O orçamento nominativo domina o contexto para que classe/situação nunca escondam
# uma coincidência forte. As faixas reproduzem a prioridade histórica da Busca V3.
VERSAO_RANKING = "ranking-busca-4.0"
SCORE_MAXIMO = 100.0

PESOS_NOMINATIVOS = {
    "Nome idêntico": 55.0,
    "Expressão completa": 40.0,
    "Elemento do nome": 8.0,
    "Radical semelhante": 6.0,
    "Variação ortográfica ou fonética": 7.0,
    "Aproximação nominativa": 3.0,
}
REGRAS_NOMINATIVAS = {
    "Nome idêntico": "NOME_IDENTICO",
    "Expressão completa": "EXPRESSAO_COMPLETA",
    "Elemento do nome": "ELEMENTO_NOMINATIVO",
    "Radical semelhante": "RADICAL_SEMELHANTE",
    "Variação ortográfica ou fonética": "VARIACAO_ORTOGRAFICA_FONETICA",
    "Aproximação nominativa": "APROXIMACAO_NOMINATIVA",
}
PESO_MAXIMO_SIMILARIDADE = 20.0
PESOS_CONTEXTO = {
    "MESMA_CLASSE": 10.0,
    "AFINIDADE_ALTA_APROVADA": 7.0,
    "AFINIDADE_MODERADA_APROVADA": 4.0,
    "SITUACAO_ATIVA": 5.0,
    "ALTO_RENOME": 10.0,
}


@dataclass(frozen=True, slots=True)
class FatorScoreBusca:
    regra: str
    peso: float
    evidencia: dict[str, object]


@dataclass(frozen=True, slots=True)
class ScoreBusca:
    total: float
    fatores: tuple[FatorScoreBusca, ...]
    versao: str = VERSAO_RANKING

    def fatores_json(self) -> list[dict[str, object]]:
        return [asdict(fator) for fator in self.fatores]


def _adicionar_fator(
    fatores: list[FatorScoreBusca],
    regra: str,
    peso: float,
    evidencia: dict[str, object],
) -> None:
    if peso > 0 and all(fator.regra != regra for fator in fatores):
        fatores.append(FatorScoreBusca(regra=regra, peso=round(peso, 2), evidencia=evidencia))


def calcular_score_nominativo(
    *,
    criterios: list[str],
    similaridade: float,
    processo: str,
    titulo: str | None,
    mesma_classe: str | None = None,
    situacao_ativa: bool = False,
) -> ScoreBusca:
    fatores: list[FatorScoreBusca] = []
    criterios_aplicados = set(criterios)

    # Identidade e expressão completa são mutuamente exclusivas no detector.
    for criterio, peso in PESOS_NOMINATIVOS.items():
        if criterio in criterios_aplicados:
            _adicionar_fator(
                fatores,
                REGRAS_NOMINATIVAS[criterio],
                peso,
                {"criterio": criterio, "titulo": titulo or "", "processo": processo},
            )

    similaridade_limitada = min(1.0, max(0.0, similaridade))
    _adicionar_fator(
        fatores,
        "SIMILARIDADE_NOMINATIVA",
        similaridade_limitada * PESO_MAXIMO_SIMILARIDADE,
        {
            "similaridade": round(similaridade_limitada, 4),
            "processo": processo,
        },
    )
    if mesma_classe:
        _adicionar_fator(
            fatores,
            "MESMA_CLASSE",
            PESOS_CONTEXTO["MESMA_CLASSE"],
            {"classe": mesma_classe, "processo": processo},
        )
    if situacao_ativa:
        _adicionar_fator(
            fatores,
            "SITUACAO_ATIVA",
            PESOS_CONTEXTO["SITUACAO_ATIVA"],
            {"situacao": "ativa", "processo": processo},
        )
    return ScoreBusca(
        total=round(min(SCORE_MAXIMO, sum(fator.peso for fator in fatores)), 2),
        fatores=tuple(fatores),
    )


def adicionar_contexto_score(
    score: ScoreBusca,
    *,
    processo: str,
    classes_atividade: list[str],
    classes_processo: list[str],
    afinidade_nivel: str | None,
    afinidade_revisao: str | None,
    situacao_ativa: bool,
    alto_renome: bool,
) -> ScoreBusca:
    fatores = list(score.fatores)
    classes_comuns = sorted(set(classes_atividade) & set(classes_processo))
    if classes_comuns:
        _adicionar_fator(
            fatores,
            "MESMA_CLASSE",
            PESOS_CONTEXTO["MESMA_CLASSE"],
            {"classes": classes_comuns, "processo": processo},
        )
    elif afinidade_revisao == "aprovada" and afinidade_nivel in {"alta", "moderada"}:
        regra = (
            "AFINIDADE_ALTA_APROVADA"
            if afinidade_nivel == "alta"
            else "AFINIDADE_MODERADA_APROVADA"
        )
        _adicionar_fator(
            fatores,
            regra,
            PESOS_CONTEXTO[regra],
            {
                "afinidade": afinidade_nivel,
                "revisao": afinidade_revisao,
                "processo": processo,
            },
        )
    if situacao_ativa:
        _adicionar_fator(
            fatores,
            "SITUACAO_ATIVA",
            PESOS_CONTEXTO["SITUACAO_ATIVA"],
            {"situacao": "ativa", "processo": processo},
        )
    if alto_renome:
        _adicionar_fator(
            fatores,
            "ALTO_RENOME",
            PESOS_CONTEXTO["ALTO_RENOME"],
            {"fonte": "lista oficial vigente", "processo": processo},
        )
    return ScoreBusca(
        total=round(min(SCORE_MAXIMO, sum(fator.peso for fator in fatores)), 2),
        fatores=tuple(fatores),
    )


def configuracao_ranking() -> dict[str, object]:
    return {
        "versao": VERSAO_RANKING,
        "escopo": "ordenacao_de_resultados; nao representa risco ou probabilidade",
        "pesos_nominativos": PESOS_NOMINATIVOS,
        "peso_maximo_similaridade": PESO_MAXIMO_SIMILARIDADE,
        "pesos_contexto": PESOS_CONTEXTO,
        "score_maximo": SCORE_MAXIMO,
        "modalidades": ["nominativa", "mista", "figurativa", "nice", "viena", "ocr", "similaridade_visual"],
        "revisao_humana_obrigatoria": True,
    }


def calcular_score_combinado(
    *, nominativo: float, visual: float = 0.0, ocr: float = 0.0,
    nice: float = 0.0, viena: float = 0.0, processo: str = "",
) -> ScoreBusca:
    """Combina sinais técnicos sem convertê-los em decisão jurídica."""
    pesos = {"nominativo": 0.45, "visual": 0.25, "ocr": 0.10, "nice": 0.10, "viena": 0.10}
    sinais = {"nominativo": nominativo, "visual": visual, "ocr": ocr, "nice": nice, "viena": viena}
    fatores = tuple(
        FatorScoreBusca(
            regra=f"SINAL_{nome.upper()}",
            peso=round(max(0.0, min(1.0, float(valor))) * 100 * pesos[nome], 2),
            evidencia={"sinal": nome, "valor": round(float(valor), 4), "processo": processo},
        )
        for nome, valor in sinais.items()
        if float(valor) > 0
    )
    return ScoreBusca(total=round(min(SCORE_MAXIMO, sum(item.peso for item in fatores)), 2), fatores=fatores, versao="ranking-combinado-1.0")

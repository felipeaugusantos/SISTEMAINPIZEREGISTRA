from dataclasses import asdict, dataclass

VERSAO_MOTOR = "deterministico-2.0"
MODO_MOTOR = "sombra"
TIPO_SCORE = "RISCO_DETERMINISTICO_POR_REGRAS"

PESOS_NOME = {
    "Nome idêntico": 40,
    "Expressão completa": 30,
    "Variação ortográfica ou fonética": 24,
    "Radical semelhante": 18,
    "Elemento do nome": 14,
    "Aproximação nominativa": 10,
}
PESOS_SITUACAO = {"ativa": 20, "incerta": 8, "inativa": -20}
PESOS_AFINIDADE = {
    ("identica", "nao_aplicavel"): 25,
    ("alta", "aprovada"): 20,
    ("alta", "pendente"): 10,
    ("moderada", "aprovada"): 12,
    ("moderada", "pendente"): 6,
}
PESO_ALTO_RENOME = 30
LIMITES = (
    (75, "critico"),
    (50, "alto"),
    (25, "moderado"),
    (0, "baixo"),
)


@dataclass(frozen=True, slots=True)
class FatorRisco:
    regra: str
    pontos: int
    evidencia: dict[str, object]


@dataclass(frozen=True, slots=True)
class ConflitoEntrada:
    numero: str
    titulo: str | None
    criterios_encontro: tuple[str, ...]
    relevancia_situacao: str | None
    situacao_normalizada: str | None
    afinidade_nivel: str | None
    afinidade_revisao: str | None
    classes_processo: tuple[str, ...]
    alto_renome: bool


@dataclass(frozen=True, slots=True)
class ConflitoPontuado:
    numero: str
    titulo: str | None
    pontuacao: int
    nivel: str
    fatores: tuple[FatorRisco, ...]
    situacao_normalizada: str | None
    classes_processo: tuple[str, ...]
    alto_renome: bool


@dataclass(frozen=True, slots=True)
class AvaliacaoDeterministica:
    pontuacao: int
    nivel: str
    principais_conflitos: tuple[ConflitoPontuado, ...]


def nivel_por_pontuacao(pontuacao: int) -> str:
    for limite, nivel in LIMITES:
        if pontuacao >= limite:
            return nivel
    return "baixo"


def pontuar_conflito(entrada: ConflitoEntrada) -> ConflitoPontuado:
    fatores: list[FatorRisco] = []

    correspondencias = [
        (PESOS_NOME[criterio], criterio) for criterio in entrada.criterios_encontro if criterio in PESOS_NOME
    ]
    if correspondencias:
        pontos, criterio = max(correspondencias)
        fatores.append(
            FatorRisco(
                "semelhanca_nome",
                pontos,
                {
                    "criterio": criterio,
                    "processo": entrada.numero,
                    "titulo": entrada.titulo or "",
                },
            )
        )

    relevancia = entrada.relevancia_situacao or "incerta"
    pontos_situacao = PESOS_SITUACAO.get(relevancia, PESOS_SITUACAO["incerta"])
    fatores.append(
        FatorRisco(
            "situacao_processual",
            pontos_situacao,
            {
                "situacao": entrada.situacao_normalizada or "nao_classificada",
                "processo": entrada.numero,
            },
        )
    )

    chave_afinidade = (
        entrada.afinidade_nivel or "sem_dados",
        entrada.afinidade_revisao or "pendente",
    )
    pontos_afinidade = PESOS_AFINIDADE.get(chave_afinidade, 0)
    if pontos_afinidade:
        fatores.append(
            FatorRisco(
                "afinidade_classes",
                pontos_afinidade,
                {
                    "nivel": chave_afinidade[0],
                    "revisao": chave_afinidade[1],
                    "classes": list(entrada.classes_processo),
                    "processo": entrada.numero,
                },
            )
        )

    if entrada.alto_renome:
        fatores.append(
            FatorRisco(
                "alto_renome",
                PESO_ALTO_RENOME,
                {
                    "fonte": "lista oficial vigente de marcas de alto renome",
                    "processo": entrada.numero,
                },
            )
        )

    pontuacao = min(100, max(0, sum(fator.pontos for fator in fatores)))
    return ConflitoPontuado(
        numero=entrada.numero,
        titulo=entrada.titulo,
        pontuacao=pontuacao,
        nivel=nivel_por_pontuacao(pontuacao),
        fatores=tuple(fatores),
        situacao_normalizada=entrada.situacao_normalizada,
        classes_processo=entrada.classes_processo,
        alto_renome=entrada.alto_renome,
    )


def calcular_risco(
    entradas: list[ConflitoEntrada],
    limite_conflitos: int = 5,
) -> AvaliacaoDeterministica:
    conflitos = sorted(
        (pontuar_conflito(entrada) for entrada in entradas),
        key=lambda conflito: (-conflito.pontuacao, conflito.numero),
    )
    pontuacao = conflitos[0].pontuacao if conflitos else 0
    return AvaliacaoDeterministica(
        pontuacao=pontuacao,
        nivel=nivel_por_pontuacao(pontuacao),
        principais_conflitos=tuple(conflitos[:limite_conflitos]),
    )


def conflito_para_json(conflito: ConflitoPontuado) -> dict:
    return asdict(conflito)


def regras_para_json() -> dict:
    return {
        "versao": VERSAO_MOTOR,
        "modo": MODO_MOTOR,
        "tipo_score": TIPO_SCORE,
        "interpretacao": (
            "Pontuação determinística de risco de conflito; não é probabilidade, "
            "estimativa histórica ou garantia de decisão do INPI."
        ),
        "pesos_nome": PESOS_NOME,
        "pesos_situacao": PESOS_SITUACAO,
        "pesos_afinidade": {f"{nivel}:{revisao}": peso for (nivel, revisao), peso in PESOS_AFINIDADE.items()},
        "peso_alto_renome": PESO_ALTO_RENOME,
        "limites": {nivel: limite for limite, nivel in LIMITES},
        "agregacao": "maior pontuacao entre os conflitos encontrados",
    }

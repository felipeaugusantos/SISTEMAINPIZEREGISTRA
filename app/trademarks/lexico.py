"""Léxico de frequência de tokens no corpus de marcas, compartilhado entre o
motor de aprendizado (distintividade intrínseca da marca) e o motor de risco/ranking
(peso semântico de um termo específico ao comparar duas marcas)."""

import json
from functools import lru_cache
from pathlib import Path

_CAMINHO_LEXICO = Path(__file__).resolve().parent / "data" / "frequencia_tokens.json"
# Frequência (em nº de marcas) na qual um token satura como "muito comum/descritivo".
ESCALA_FREQUENCIA_TOKEN = 20000.0
# Abaixo deste limiar de nº de marcas, o token já é considerado de uso comum/evocativo
# do nicho (não precisa saturar a escala inteira para deixar de ser "raro").
LIMIAR_TERMO_COMUM = 4000
LIMIAR_TERMO_EVOCATIVO = 600
# Palavras funcionais ignoradas na frequência: elas aparecem em quase toda marca
# multivocabular e saturariam o sinal de descritividade sem informar distintividade.
STOPWORDS_FREQUENCIA = frozenset(
    {
        "DE",
        "DO",
        "DA",
        "DOS",
        "DAS",
        "E",
        "O",
        "A",
        "OS",
        "AS",
        "EM",
        "NO",
        "NA",
        "COM",
        "PARA",
        "POR",
        "UM",
        "UMA",
        "AO",
        "THE",
        "OF",
        "AND",
    }
)


@lru_cache(maxsize=1)
def frequencias_tokens() -> dict[str, int]:
    """Léxico token -> nº de marcas que o contêm, gerado por app.cli.gerar_lexico_frequencia.

    Ausente/vazio => a feature de frequência fica neutra (0), sem quebrar treino/serviço.
    """
    try:
        return json.loads(_CAMINHO_LEXICO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def classificar_termo(token: str) -> str:
    """Classifica um token normalizado em "comum", "evocativo" ou "distintivo".

    Base para descontar o peso de conflitos disparados apenas por termo de uso
    comum na classe/nicho (ex.: "PLUS", "PREMIUM", "TECH"), em vez de tratá-lo
    com o mesmo peso de um radical raro/fantasioso.

    NOTA: duas tentativas de estender isto para radicais truncados ambíguos (ex.:
    "SINA" -> "SINAFRESP") foram avaliadas e descartadas por não bater com os dados
    reais do léxico (nem colisão de truncamento nem contagem de prefixo capturam o
    problema -- o léxico simplesmente não tem cobertura densa o suficiente para
    esses radicais curtos). O sinal real parece ser o COMPRIMENTO do radical, não
    sua frequência no corpus; ver discussão de sessão antes de tentar de novo.
    """
    if token in STOPWORDS_FREQUENCIA:
        return "comum"
    frequencia = frequencias_tokens().get(token, 0)
    if frequencia >= LIMIAR_TERMO_COMUM:
        return "comum"
    if frequencia >= LIMIAR_TERMO_EVOCATIVO:
        return "evocativo"
    return "distintivo"

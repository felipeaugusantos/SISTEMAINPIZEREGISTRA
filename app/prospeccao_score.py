"""Score comercial do Prospect -- Fase 5 do Radar de Prospecção (03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md).

Fatores explícitos e versionados (mesmo padrão de app.search_ranking):
nenhum score é caixa-preta -- score_detalhe sempre guarda regra + peso +
evidência de cada fator, auditável depois pela equipe comercial.

NOTA: o roadmap original também previa um fator de "afinidade CNAE↔NCL",
mas esse cruzamento não existe no sistema -- app.trademarks.affinity compara
dois conjuntos de classes NCL entre si (atividade do requerente vs. processo
pesquisado), não CNAE contra NCL. CNAE tem ~1300 códigos e NCL 45 classes;
um cruzamento confiável entre os dois exigiria curadoria própria, que não
existe hoje. Fator fica de fora até essa base de dados existir -- peso
redistribuído aos demais, documentado aqui, não omitido silenciosamente.
"""

from dataclasses import asdict, dataclass
from datetime import date

VERSAO_SCORE = "1.0.0"
SCORE_MAXIMO = 100.0

PESO_SITUACAO_ATIVA = 30.0
PESO_TEMPO_ABERTURA = 25.0
PESO_PRESENCA_DIGITAL = 15.0
PESO_TRIAGEM_MARCA = 30.0
ANOS_SATURACAO_TEMPO_ABERTURA = 5.0

# Fração do peso de TRIAGEM_MARCA concedida a cada classificação -- quanto
# mais "limpo" o resultado da triagem, mais fácil a venda (sem conflito
# aparente); um conflito real não zera o prospect (ainda pode virar uma boa
# conversa comercial), mas pontua menos que um caminho livre.
FRACAO_TRIAGEM = {
    "nao_localizado": 1.0,
    "resultado_semelhante": 0.6,
    "resultado_relevante_localizado": 0.5,
    "inconclusivo": 0.4,
    "analise_humana_necessaria": 0.25,
}


@dataclass(frozen=True, slots=True)
class FatorScoreProspect:
    regra: str
    peso: float
    evidencia: dict[str, object]


@dataclass(frozen=True, slots=True)
class ScoreProspect:
    total: float
    fatores: tuple[FatorScoreProspect, ...]
    versao: str = VERSAO_SCORE

    def fatores_json(self) -> list[dict[str, object]]:
        return [asdict(fator) for fator in self.fatores]


def calcular_score(
    *,
    situacao_cadastral: str | None,
    data_abertura: date | None,
    presenca_digital: dict | None,
    triagem_marca_status: str | None,
    hoje: date | None = None,
) -> ScoreProspect:
    hoje = hoje or date.today()
    fatores: list[FatorScoreProspect] = []

    if situacao_cadastral == "ativa":
        fatores.append(
            FatorScoreProspect(
                "SITUACAO_CADASTRAL_ATIVA", PESO_SITUACAO_ATIVA, {"situacao_cadastral": situacao_cadastral}
            )
        )

    if data_abertura is not None:
        anos = max(0.0, (hoje - data_abertura).days / 365.25)
        fracao = min(1.0, anos / ANOS_SATURACAO_TEMPO_ABERTURA)
        peso = round(fracao * PESO_TEMPO_ABERTURA, 2)
        if peso > 0:
            fatores.append(FatorScoreProspect("TEMPO_DE_ABERTURA", peso, {"anos": round(anos, 1)}))

    if presenca_digital and presenca_digital.get("ativo") is True:
        fatores.append(FatorScoreProspect("PRESENCA_DIGITAL_ATIVA", PESO_PRESENCA_DIGITAL, {"site_ativo": True}))

    if triagem_marca_status:
        fracao = FRACAO_TRIAGEM.get(triagem_marca_status, 0.0)
        peso = round(fracao * PESO_TRIAGEM_MARCA, 2)
        if peso > 0:
            fatores.append(
                FatorScoreProspect("TRIAGEM_DE_MARCA", peso, {"classificacao": triagem_marca_status})
            )

    total = round(min(SCORE_MAXIMO, sum(fator.peso for fator in fatores)), 2)
    return ScoreProspect(total=total, fatores=tuple(fatores))

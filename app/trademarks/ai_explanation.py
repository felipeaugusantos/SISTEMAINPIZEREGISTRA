import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.models import AvaliacaoRiscoMarca
from app.settings import Settings

VERSAO_PROMPT = "explicacao-risco-1.0"
RegraRisco = Literal[
    "semelhanca_nome",
    "situacao_processual",
    "afinidade_classes",
    "alto_renome",
]
LimitacaoExplicacao = Literal[
    "indicativo_nao_conclusivo",
    "nao_substitui_analise_humana",
    "baseado_apenas_no_motor_deterministico",
]

TERMOS_JURIDICOS_PROIBIDOS = re.compile(
    r"\b(?:art(?:igo)?\.?|lei|lpi|jurisprud[eê]ncia|s[uú]mula|inciso|al[ií]nea)\b",
    re.IGNORECASE,
)
REFERENCIA_CLASSE_PROIBIDA = re.compile(
    r"\b(?:classe|ncl|nice)\s*[-:]?\s*\d+",
    re.IGNORECASE,
)
ENTIDADE_PROIBIDA = re.compile(
    r"\b(?:processo(?:s)?|marca(?:s)?|titular(?:es)?|empresa(?:s)?|classe(?:s)?|ncl|nice)\b",
    re.IGNORECASE,
)
FUNDAMENTO_PROIBIDO = re.compile(
    r"\b(?:fundamento(?:s)?|jur[ií]dic[oa]s?|legal|legisla[cç][aã]o)\b",
    re.IGNORECASE,
)
NUMERO_TEXTO = re.compile(r"(?<!\w)-?\d+(?!\w)")


class FatorEntradaIA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regra: RegraRisco
    pontos: int
    evidencia_controlada: str


class ConflitoEntradaIA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflito_id: int = Field(ge=1)
    pontuacao: int = Field(ge=0, le=100)
    nivel: Literal["baixo", "moderado", "alto", "critico"]
    fatores: list[FatorEntradaIA]


class EntradaExplicacaoIA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versao_motor: str
    pontuacao: int = Field(ge=0, le=100)
    nivel: Literal["baixo", "moderado", "alto", "critico"]
    regra_agregacao: str
    conflitos: list[ConflitoEntradaIA]


class ExplicacaoConflitoIA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflito_id: int = Field(ge=1)
    regras_referenciadas: list[RegraRisco]
    explicacao: str = Field(min_length=10, max_length=1000)


class SaidaExplicacaoIA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pontuacao: int = Field(ge=0, le=100)
    nivel: Literal["baixo", "moderado", "alto", "critico"]
    resumo_executivo: str = Field(min_length=20, max_length=1200)
    leitura_pontuacao: str = Field(min_length=20, max_length=1200)
    conflitos: list[ExplicacaoConflitoIA]
    limitacoes: list[LimitacaoExplicacao]


@dataclass(frozen=True, slots=True)
class ResultadoGeracaoIA:
    saida: SaidaExplicacaoIA
    resposta_id: str | None


class ConfiguracaoIAInativaError(RuntimeError):
    pass


class SaidaIAInvalidaError(RuntimeError):
    pass


def construir_entrada_anonimizada(avaliacao: AvaliacaoRiscoMarca) -> EntradaExplicacaoIA:
    conflitos: list[ConflitoEntradaIA] = []
    for indice, conflito in enumerate(avaliacao.principais_conflitos or [], start=1):
        fatores = [
            FatorEntradaIA(
                regra=fator["regra"],
                pontos=int(fator["pontos"]),
                evidencia_controlada=str(fator["evidencia"]),
            )
            for fator in conflito.get("fatores", [])
        ]
        conflitos.append(
            ConflitoEntradaIA(
                conflito_id=indice,
                pontuacao=int(conflito["pontuacao"]),
                nivel=conflito["nivel"],
                fatores=fatores,
            )
        )
    return EntradaExplicacaoIA(
        versao_motor=avaliacao.versao_motor,
        pontuacao=avaliacao.pontuacao,
        nivel=avaliacao.nivel,
        regra_agregacao=str(
            (avaliacao.regras_aplicadas or {}).get(
                "agregacao",
                "maior pontuacao entre os conflitos encontrados",
            )
        ),
        conflitos=conflitos,
    )


def hash_entrada(entrada: EntradaExplicacaoIA) -> str:
    conteudo = json.dumps(
        entrada.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def exige_revisao_humana(nivel: str) -> bool:
    return nivel in {"alto", "critico"}


def _textos_saida(saida: SaidaExplicacaoIA) -> list[str]:
    return [
        saida.resumo_executivo,
        saida.leitura_pontuacao,
        *(conflito.explicacao for conflito in saida.conflitos),
    ]


def validar_saida_contra_entrada(
    entrada: EntradaExplicacaoIA,
    saida: SaidaExplicacaoIA,
) -> None:
    if saida.pontuacao != entrada.pontuacao or saida.nivel != entrada.nivel:
        raise SaidaIAInvalidaError("A IA alterou a pontuação ou o nível determinístico")

    esperados = {conflito.conflito_id: conflito for conflito in entrada.conflitos}
    recebidos = {conflito.conflito_id: conflito for conflito in saida.conflitos}
    if len(recebidos) != len(saida.conflitos) or recebidos.keys() != esperados.keys():
        raise SaidaIAInvalidaError("A IA omitiu, duplicou ou inventou um conflito")

    for conflito_id, conflito_saida in recebidos.items():
        regras_esperadas = {fator.regra for fator in esperados[conflito_id].fatores}
        regras_recebidas = set(conflito_saida.regras_referenciadas)
        if regras_recebidas != regras_esperadas:
            raise SaidaIAInvalidaError("A IA omitiu ou inventou uma regra de pontuação")

    numeros_permitidos = {
        entrada.pontuacao,
        *(conflito.conflito_id for conflito in entrada.conflitos),
        *(conflito.pontuacao for conflito in entrada.conflitos),
        *(
            fator.pontos
            for conflito in entrada.conflitos
            for fator in conflito.fatores
        ),
    }
    for texto in _textos_saida(saida):
        if TERMOS_JURIDICOS_PROIBIDOS.search(texto):
            raise SaidaIAInvalidaError("A IA acrescentou fundamento jurídico não autorizado")
        if FUNDAMENTO_PROIBIDO.search(texto):
            raise SaidaIAInvalidaError("A IA acrescentou linguagem jurídica não autorizada")
        if ENTIDADE_PROIBIDA.search(texto):
            raise SaidaIAInvalidaError("A IA acrescentou entidade ou classe não autorizada")
        if REFERENCIA_CLASSE_PROIBIDA.search(texto):
            raise SaidaIAInvalidaError("A IA acrescentou classe não autorizada")
        numeros_encontrados = {int(numero) for numero in NUMERO_TEXTO.findall(texto)}
        if not numeros_encontrados.issubset(numeros_permitidos):
            raise SaidaIAInvalidaError("A IA acrescentou referência numérica não autorizada")


SYSTEM_PROMPT = """
Você explica, em português do Brasil, uma pontuação determinística de risco marcário.
Não faça uma nova avaliação e não altere pontuação, nível, conflitos ou regras.
Use somente os dados estruturados recebidos.
Não cite marcas, pessoas, empresas, processos, classes, leis, artigos, incisos, jurisprudência
ou fundamentos jurídicos. Não ofereça conclusão sobre registrabilidade.
Explique todos os fatores de todos os conflitos, referenciando somente conflito_id e as regras
fornecidas. Use linguagem clara, cautelosa e informativa.
""".strip()


async def gerar_explicacao(
    entrada: EntradaExplicacaoIA,
    settings: Settings,
) -> ResultadoGeracaoIA:
    if not settings.ai_explanations_enabled or not settings.openai_api_key:
        raise ConfiguracaoIAInativaError(
            "A IA explicativa está desativada ou sem OPENAI_API_KEY configurada"
        )

    cliente = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=30.0,
        max_retries=1,
    )
    resposta = await cliente.responses.parse(
        model=settings.openai_explanation_model,
        store=False,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    entrada.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ],
        text_format=SaidaExplicacaoIA,
    )
    if resposta.output_parsed is None:
        raise SaidaIAInvalidaError("O provedor não retornou uma explicação estruturada")
    saida = SaidaExplicacaoIA.model_validate(resposta.output_parsed)
    validar_saida_contra_entrada(entrada, saida)
    return ResultadoGeracaoIA(saida=saida, resposta_id=getattr(resposta, "id", None))

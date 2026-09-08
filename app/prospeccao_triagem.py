"""Triagem de marca para Prospect -- SOMENTE indicativa, nunca definitiva
(Fase 4 do Radar de Prospecção, 03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md).

IMPORTANTE (exigência explícita do usuário): este módulo NUNCA pode afirmar
que uma marca está disponível para registro. As únicas classificações
possíveis são as do enum abaixo -- "disponível"/"livre" não existe nesse
vocabulário de propósito, não é uma regra de UI que pode ser esquecida, é a
única coisa que o enum permite devolver. A exceção é JA_E_TITULAR, que não é
um parecer de registrabilidade e sim um fato de banco de dados (o próprio
prospect já consta como titular de um processo com esse nome).

Reaproveita o motor de busca já existente (app.search.buscar_marcas) e o
léxico de peso semântico (app.trademarks.lexico, via
app.search.termos_comuns_do_match) -- não duplica nenhuma infraestrutura.
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crm import normalizar_empresa
from app.inpi_titular_live import buscar_titularidade_inpi_ao_vivo
from app.models import Processo, Titular
from app.search import OcorrenciaBusca, buscar_marcas, termos_comuns_do_match


@dataclass
class TitularidadeAmpla:
    """Sinal de que o prospect já é titular de ALGUMA marca no INPI, de uma
    das duas fontes possíveis -- nunca as duas ao mesmo tempo (a local é
    checada primeiro; a busca ao vivo só roda se a local não achar nada)."""

    descricao: str


class ClassificacaoTriagemProspect(StrEnum):
    NAO_LOCALIZADO = "nao_localizado"
    RESULTADO_SEMELHANTE = "resultado_semelhante"
    RESULTADO_RELEVANTE_LOCALIZADO = "resultado_relevante_localizado"
    INCONCLUSIVO = "inconclusivo"
    ANALISE_HUMANA_NECESSARIA = "analise_humana_necessaria"
    # Achado da sessão de 05/09/2026: sinal factual (não é parecer de
    # registrabilidade) -- o próprio prospect já consta como titular de um
    # processo com nome idêntico ao pesquisado. Diferente das outras 5
    # classificações, esta habilita descarte rápido na tela ("já tem marca
    # registrada, não precisa da gente").
    JA_E_TITULAR = "ja_e_titular"
    # Achado da sessão de 08/09/2026: diferente de JA_E_TITULAR (restrito à
    # marca especificamente pesquisada), este sinal vem de uma checagem ampla
    # e incondicional na base de titulares -- "o prospect já registra ALGUMA
    # marca no INPI", útil mesmo quando a marca pesquisada não bate com nada.
    POSSUI_OUTRA_MARCA_REGISTRADA = "possui_outra_marca_registrada"


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


def _prospect_e_titular(ocorrencias: list[OcorrenciaBusca], razao_social: str) -> OcorrenciaBusca | None:
    """Acha a primeira ocorrência em que o próprio prospect (pela razão social)
    já consta como titular -- sinal factual (não é parecer de registrabilidade),
    usado só para oferecer descarte rápido na tela ("já tem marca, não precisa
    da gente"). Compara nome normalizado (mesmo padrão de
    verificar_conflito_interesse, app/crm.py) -- falso negativo (grafia muito
    diferente) é seguro, cai na classificação normal; falso positivo é raro
    porque exige nome completo idêntico após normalização."""
    razao_normalizada = normalizar_empresa(razao_social or "")
    if not razao_normalizada:
        return None
    for ocorrencia in ocorrencias:
        titulares = getattr(ocorrencia.processo, "titulares", None) or []
        if any(normalizar_empresa(titular.nome or "") == razao_normalizada for titular in titulares):
            return ocorrencia
    return None


async def buscar_titularidade_ampla(
    session: AsyncSession, razao_social: str | None, nome_fantasia: str | None, cnpj: str | None = None
) -> TitularidadeAmpla | None:
    """Verifica se o prospect já consta como titular de QUALQUER marca no
    INPI -- checagem incondicional, não depende da marca especificamente
    pesquisada (diferente de _prospect_e_titular, que só olha as ocorrências
    da busca atual). Duas fontes, nessa ordem:

    1. Base local (nome normalizado, sincronizada semanalmente via RPI --
       ver migrations/versions/jd75y0f6r397_titular_nome_normalizado.py).
       Sem CNPJ estruturado nos dados do INPI, o match é por nome (mesma
       normalização usada em toda a base, ver normalizar_empresa).
    2. Se a local não achar nada e um CNPJ foi informado: busca ao vivo no
       site público do INPI por CNPJ (mais precisa, mas best-effort -- ver
       app.inpi_titular_live, sistema legado confirmado instável)."""
    nomes = {
        normalizado
        for normalizado in (normalizar_empresa(razao_social or ""), normalizar_empresa(nome_fantasia or ""))
        if normalizado
    }
    if nomes:
        consulta = select(Processo).join(Processo.titulares).where(Titular.nome_normalizado.in_(nomes)).limit(1)
        processo = (await session.execute(consulta)).scalars().first()
        if processo is not None:
            return TitularidadeAmpla(descricao=f'"{processo.titulo}" (processo {processo.numero})')

    titular_inpi = await buscar_titularidade_inpi_ao_vivo(cnpj)
    if titular_inpi:
        return TitularidadeAmpla(
            descricao=f'titular "{titular_inpi}" localizado na busca ao vivo do INPI por CNPJ'
        )
    return None


def classificar(
    total_resultados: int,
    ocorrencias: list[OcorrenciaBusca],
    marca_candidata: str,
    razao_social: str | None = None,
    titular_qualquer_marca: TitularidadeAmpla | None = None,
) -> tuple[ClassificacaoTriagemProspect, str]:
    """Decide a classificação a partir do resultado já calculado por
    buscar_marcas() -- não roda nenhuma query, só interpreta.

    O gate de volume (`total_resultados > LIMITE_BUSCA_TRIAGEM`) usava a
    contagem BRUTA de buscar_marcas -- que inclui radicais fonéticos via
    ILIKE '%termo%' sem limite de palavra (ex.: o radical "MOCOC" de "MOCOCA"
    bate em qualquer título que contenha essa sequência). Uma marca/empresa
    com um termo de alta frequência no corpus estourava os 50 resultados e
    caía direto em "análise humana necessária", mesmo quando a ocorrência
    mais relevante (já ordenada e pontuada com o desconto de termo comum) não
    indica conflito real. Achado de 05/09/2026, escopo só desta triagem (não
    mexe em app.search.buscar_marcas, usado também pela pesquisa formal).
    """
    if ocorrencias and razao_social:
        titular_match = _prospect_e_titular(ocorrencias, razao_social)
        if titular_match is not None:
            return (
                ClassificacaoTriagemProspect.JA_E_TITULAR,
                f'O próprio prospect já consta como titular do processo {titular_match.processo.numero} '
                f'("{titular_match.processo.titulo}") -- confirme antes de descartar, mas pode já ter a '
                "marca registrada e não precisar de um novo depósito.",
            )
    if titular_qualquer_marca is not None:
        return (
            ClassificacaoTriagemProspect.POSSUI_OUTRA_MARCA_REGISTRADA,
            f"O prospect já consta como titular de outra marca no INPI: {titular_qualquer_marca.descricao} "
            "-- não necessariamente a marca pesquisada aqui, mas indica que a empresa já registra marca(s) "
            "e conhece o processo.",
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

    volume_alto = total_resultados > LIMITE_BUSCA_TRIAGEM
    melhor, referencia = _melhor_ocorrencia_e_justificativa(ocorrencias)
    termos_comuns = termos_comuns_do_match(melhor.processo.titulo, marca_candidata)
    if termos_comuns and melhor.score.total < LIMIAR_RESULTADO_RELEVANTE:
        if volume_alto:
            return (
                ClassificacaoTriagemProspect.NAO_LOCALIZADO,
                f'A busca por "{marca_candidata}" encontrou mais de {LIMITE_BUSCA_TRIAGEM} ocorrências, mas a '
                f"mais próxima ({referencia}) só coincide em termo(s) de uso comum "
                f"({', '.join(termos_comuns)}) -- volume alto explicado por termo comum, sem indício de "
                "conflito real.",
            )
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
    if volume_alto:
        return (
            ClassificacaoTriagemProspect.ANALISE_HUMANA_NECESSARIA,
            f'A busca por "{marca_candidata}" encontrou mais de {LIMITE_BUSCA_TRIAGEM} ocorrências e a mais '
            f"próxima ({referencia}) tem semelhança fraca -- volume alto demais para descartar automaticamente.",
        )
    return (
        ClassificacaoTriagemProspect.INCONCLUSIVO,
        f"As ocorrências encontradas (a mais próxima: {referencia}) têm semelhança fraca com o "
        "termo pesquisado -- necessária análise humana.",
    )


async def triar_marca_prospect(
    session: AsyncSession, razao_social: str, nome_fantasia: str | None, cnpj: str | None = None
) -> dict:
    """Roda a triagem e devolve o dict pronto para virar ProspectTriagem.
    Não grava nada -- quem chama decide persistir (ver app/worker.py)."""
    titular_qualquer_marca = await buscar_titularidade_ampla(session, razao_social, nome_fantasia, cnpj)

    marca_candidata = extrair_marca_candidata(razao_social, nome_fantasia)
    nome_pesquisado = (nome_fantasia or razao_social or "").strip()[:200] or "(sem nome)"
    if not marca_candidata or len(marca_candidata) < 2:
        if titular_qualquer_marca is None:
            return {
                "marca_pesquisada": nome_pesquisado,
                "classificacao": ClassificacaoTriagemProspect.ANALISE_HUMANA_NECESSARIA.value,
                "justificativa": "O nome da empresa não permite compor um termo de busca significativo "
                "(sem nome fantasia e razão social só com termos societários genéricos).",
                "total_resultados": 0,
            }
        total, ocorrencias = 0, []
    else:
        total, ocorrencias, _evidencias = await buscar_marcas(
            session, marca_candidata, tipo_pesquisa="completa", classe_nice=None, limite=LIMITE_BUSCA_TRIAGEM
        )
    classificacao, justificativa = classificar(
        total, ocorrencias, marca_candidata or nome_pesquisado, razao_social, titular_qualquer_marca
    )
    return {
        "marca_pesquisada": marca_candidata[:200] if marca_candidata else nome_pesquisado,
        "classificacao": classificacao.value,
        "justificativa": justificativa,
        "total_resultados": total,
    }

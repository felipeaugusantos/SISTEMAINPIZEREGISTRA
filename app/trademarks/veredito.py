"""Veredito público único da análise de registrabilidade.

Fase 1 da auditoria (04/09/2026, achados REG-1 a REG-6): existiam 4 caminhos
paralelos calculando "favorável"/"desfavorável" de forma independente
(construir_conclusao em pesquisas.py, analisar_registrabilidade.decisao em
agent.py, construir_prognostico_registrabilidade.veredito em
registrability.py, apresentacao_analise.situacao em consolidated.py) --
nenhum deles impedia que ausência de evidência (probabilidade None, critério
não analisado) virasse "favorável" por omissão, e um deles ("atencao") usava
um quarto veredito público que não existe nas regras adotadas aqui.

Este módulo é o ÚNICO ponto que decide o veredito público (FAVORAVEL /
DESFAVORAVEL / INCONCLUSIVO). Os quatro caminhos antigos continuam existindo
(mantidos por compatibilidade -- decisão formal de 04/09/2026) mas passam a
ser consumidos como INSUMO deste motor, não mais como veredito final exibido
ao cliente.

Regras formais adotadas em 04/09/2026 (decisão do responsável pelo produto,
registrada aqui conforme exigido -- não é regra jurídica nova, é política de
apresentação de um resultado que já existia):

1. Falha ou indisponibilidade de busca -> INCONCLUSIVO.
2. Cobertura de evidências abaixo do mínimo (mesmo limiar já usado em
   agent.COBERTURA_MINIMA, 60%) -> INCONCLUSIVO.
3. Qualquer critério substantivo ainda não analisado -> INCONCLUSIVO (nunca
   "favorável por omissão" -- corrige REG-3).
4. Impedimento crítico rastreável (regra Art.124 da matriz determinística OU
   nível de risco alto/crítico do motor de conflitos, sempre com motivo
   nomeado) -> DESFAVORAVEL. Nunca por probabilidade estatística isolada.
5. Pontos de atenção (regra "alerta" na matriz) sem impedimento -> não vira
   veredito próprio (corrige REG-5, "atencao" deixa de ser um 4o veredito) --
   fica INCONCLUSIVO, com os pontos de atenção anexados como evidência.
6. Enquanto nenhum modelo estiver em status ACTIVE, o veredito público nunca
   é FAVORAVEL (corrige REG-2 e REG-4 -- modelo SHADOW/VALIDATION ou ausente
   não determina veredito público). Hoje (04/09/2026) são 14/14 modelos em
   SHADOW, então nenhuma análise deveria virar FAVORAVEL até a promoção
   formal e deliberada de um modelo a ACTIVE (regra 9 do playbook de
   auditoria -- não é este código que promove modelos).
7. FAVORAVEL exige, simultaneamente: busca concluída, cobertura suficiente,
   nenhum critério pendente, base identificada, nenhum impedimento crítico,
   modelo ACTIVE e probabilidade de deferimento >= limiar já estabelecido
   (0.70, o mesmo já usado em agent.py antes desta correção).
8. Parecer humano (quando registrado) tem poder de override total sobre o
   veredito automático, com auditoria (quem, quando) -- nunca é descartado
   silenciosamente.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.trademarks.registrability import IMPEDIMENTO_CODIGOS, construir_prognostico_registrabilidade

LIMIAR_PROBABILIDADE_FAVORAVEL = 0.70
COBERTURA_MINIMA = 0.60
VERSAO_MOTOR_VEREDITO = "veredito-publico-1.0"


class VeredictoPublico(StrEnum):
    FAVORAVEL = "FAVORAVEL"
    DESFAVORAVEL = "DESFAVORAVEL"
    INCONCLUSIVO = "INCONCLUSIVO"


@dataclass(frozen=True, slots=True)
class MotivoVeredito:
    codigo: str
    descricao: str


@dataclass(frozen=True, slots=True)
class EntradaVeredito:
    busca_concluida: bool
    busca_falhou: bool
    base_identificada: bool
    cobertura_evidencias: float
    pendencias: tuple[str, ...]
    impedimentos_criticos: tuple[MotivoVeredito, ...]
    pontos_atencao: tuple[MotivoVeredito, ...]
    modelo_status: str | None
    probabilidade_deferimento: float | None
    cobertura_minima: float = COBERTURA_MINIMA
    limiar_favoravel: float = LIMIAR_PROBABILIDADE_FAVORAVEL


@dataclass(frozen=True, slots=True)
class ResultadoVeredito:
    veredito: VeredictoPublico
    motivo_principal: str
    motivos: tuple[MotivoVeredito, ...]
    pontos_atencao: tuple[MotivoVeredito, ...]
    origem: str  # "motor_automatico" | "parecer_humano"


def determinar_veredito_publico(entrada: EntradaVeredito) -> ResultadoVeredito:
    """Único ponto de decisão do veredito público automático.

    Ordem de avaliação é a própria especificação da regra: falha de busca >
    cobertura > pendências > impedimento crítico > modelo/base > favorável.
    """
    if entrada.busca_falhou:
        return _inconclusivo("Falha na busca de anterioridades -- não é possível concluir sem uma base confiável.")
    if not entrada.busca_concluida:
        return _inconclusivo("Busca de anterioridades ainda não concluída.")
    if entrada.cobertura_evidencias < entrada.cobertura_minima:
        return _inconclusivo(
            f"Cobertura de evidências ({round(entrada.cobertura_evidencias * 100)}%) "
            f"abaixo do mínimo exigido ({round(entrada.cobertura_minima * 100)}%)."
        )
    if entrada.pendencias:
        return _inconclusivo(
            "Há critério(s) técnico(s) ainda não analisado(s): " + ", ".join(entrada.pendencias) + ".",
            pontos_atencao=entrada.pontos_atencao,
        )
    if entrada.impedimentos_criticos:
        return ResultadoVeredito(
            veredito=VeredictoPublico.DESFAVORAVEL,
            motivo_principal="Impedimento identificado nas regras do INPI ou nos conflitos de busca.",
            motivos=entrada.impedimentos_criticos,
            pontos_atencao=entrada.pontos_atencao,
            origem="motor_automatico",
        )
    if not entrada.base_identificada:
        return _inconclusivo("Base normativa/classe não identificada com segurança suficiente.", pontos_atencao=entrada.pontos_atencao)
    if entrada.pontos_atencao:
        return _inconclusivo(
            "Pontos de atenção identificados exigem conferência técnica antes de uma conclusão.",
            pontos_atencao=entrada.pontos_atencao,
        )
    if entrada.modelo_status != "ACTIVE":
        return _inconclusivo(
            "Aguardando validação estatística -- nenhum modelo em status ACTIVE no momento desta análise."
        )
    if entrada.probabilidade_deferimento is None:
        return _inconclusivo("Modelo ativo não retornou probabilidade calculada para este caso.")
    if entrada.probabilidade_deferimento < entrada.limiar_favoravel:
        return _inconclusivo(
            f"Probabilidade estimada ({round(entrada.probabilidade_deferimento * 100)}%) "
            f"abaixo do limiar de confiança para conclusão favorável ({round(entrada.limiar_favoravel * 100)}%)."
        )
    return ResultadoVeredito(
        veredito=VeredictoPublico.FAVORAVEL,
        motivo_principal=(
            "Busca concluída, base identificada, nenhum impedimento crítico e modelo ativo "
            "com probabilidade de deferimento acima do limiar de confiança."
        ),
        motivos=(),
        pontos_atencao=entrada.pontos_atencao,
        origem="motor_automatico",
    )


def _inconclusivo(motivo_principal: str, *, pontos_atencao: tuple[MotivoVeredito, ...] = ()) -> ResultadoVeredito:
    return ResultadoVeredito(
        veredito=VeredictoPublico.INCONCLUSIVO,
        motivo_principal=motivo_principal,
        motivos=(),
        pontos_atencao=pontos_atencao,
        origem="motor_automatico",
    )


def montar_entrada_veredito(
    *,
    matriz: dict,
    qualidade_bloqueada: bool,
    cobertura_evidencias: float,
    base_identificada: bool,
    nivel_risco: str | None,
    modelo_status: str | None,
    probabilidade_deferimento: float | None,
) -> EntradaVeredito:
    """Monta a entrada do motor a partir dos dados já calculados pelo
    relatório (matriz determinística, qualidade da base, risco de conflitos,
    modelo estatístico) -- reaproveita construir_prognostico_registrabilidade
    como ÚNICA fonte de impedimentos/pontos-de-atenção/pendências, em vez de
    cada consumidor (tela, PDF, motor de veredito) recalcular sua própria
    lista a partir da matriz (corrige REG-6, risco de mensagens duplicadas
    entre painéis que liam a mesma matriz de formas independentes)."""
    prognostico = construir_prognostico_registrabilidade(matriz)
    por_codigo = {regra.get("codigo"): regra for regra in matriz.get("regras") or []}
    relevantes = [por_codigo[codigo] for codigo in IMPEDIMENTO_CODIGOS if codigo in por_codigo]

    impedimentos_criticos = tuple(
        MotivoVeredito(codigo=regra["codigo"], descricao=regra.get("conclusao") or regra.get("criterio", ""))
        for regra in relevantes
        if regra["status"] == "possivel_impedimento"
    )
    if not impedimentos_criticos and nivel_risco in {"alto", "critico"}:
        # Risco de conflitos de busca (risk.py) é uma fonte de impedimento
        # rastreável distinta da matriz Art.124 -- também nunca decide o
        # veredito sem um motivo nomeado.
        impedimentos_criticos = (
            MotivoVeredito(
                codigo="risco_conflitos_busca",
                descricao=f"Nível de risco de conflitos de busca: {nivel_risco}.",
            ),
        )
    pontos_atencao = tuple(
        MotivoVeredito(codigo=regra["codigo"], descricao=regra.get("conclusao") or regra.get("criterio", ""))
        for regra in relevantes
        if regra["status"] == "alerta"
    )

    return EntradaVeredito(
        busca_concluida=True,
        busca_falhou=qualidade_bloqueada,
        base_identificada=base_identificada,
        cobertura_evidencias=cobertura_evidencias,
        pendencias=tuple(prognostico["pendencias"]),
        impedimentos_criticos=impedimentos_criticos,
        pontos_atencao=pontos_atencao,
        modelo_status=modelo_status,
        probabilidade_deferimento=probabilidade_deferimento,
    )


def aplicar_parecer_humano(
    resultado_automatico: ResultadoVeredito,
    *,
    veredito_humano: VeredictoPublico,
    justificativa: str,
    avaliador: str,
) -> ResultadoVeredito:
    """Override explícito do especialista. O veredito automático não é
    descartado silenciosamente -- fica preservado no chamador (ver
    veredito_motor_automatico em RelatorioMarcaResponse) para auditoria."""
    if not justificativa.strip():
        raise ValueError("Parecer humano exige justificativa registrada (auditoria).")
    if not avaliador.strip():
        raise ValueError("Parecer humano exige identificação de quem avaliou (auditoria).")
    return ResultadoVeredito(
        veredito=veredito_humano,
        motivo_principal=f"Avaliação do especialista ({avaliador}): {justificativa.strip()}",
        motivos=resultado_automatico.motivos,
        pontos_atencao=resultado_automatico.pontos_atencao,
        origem="parecer_humano",
    )

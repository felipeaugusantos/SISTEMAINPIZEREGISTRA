from enum import StrEnum


class EstadoAnalise(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    VALIDATED = "VALIDATED"


class AcaoWorkflowAnalise(StrEnum):
    SUBMIT_REVIEW = "SUBMIT_REVIEW"
    START_REVIEW = "START_REVIEW"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    VALIDATE = "VALIDATE"
    REOPEN = "REOPEN"


TRANSICOES_ANALISE: dict[
    tuple[EstadoAnalise, AcaoWorkflowAnalise], EstadoAnalise
] = {
    (EstadoAnalise.DRAFT, AcaoWorkflowAnalise.SUBMIT_REVIEW): EstadoAnalise.PENDING_REVIEW,
    (
        EstadoAnalise.PENDING_REVIEW,
        AcaoWorkflowAnalise.START_REVIEW,
    ): EstadoAnalise.IN_REVIEW,
    (
        EstadoAnalise.CHANGES_REQUESTED,
        AcaoWorkflowAnalise.START_REVIEW,
    ): EstadoAnalise.IN_REVIEW,
    (
        EstadoAnalise.IN_REVIEW,
        AcaoWorkflowAnalise.REQUEST_CHANGES,
    ): EstadoAnalise.CHANGES_REQUESTED,
    (EstadoAnalise.IN_REVIEW, AcaoWorkflowAnalise.VALIDATE): EstadoAnalise.VALIDATED,
    (EstadoAnalise.VALIDATED, AcaoWorkflowAnalise.REOPEN): EstadoAnalise.IN_REVIEW,
}


def proximo_estado_analise(
    estado_atual: str | EstadoAnalise,
    acao: str | AcaoWorkflowAnalise,
) -> EstadoAnalise:
    try:
        estado = EstadoAnalise(estado_atual)
        acao_normalizada = AcaoWorkflowAnalise(acao)
    except ValueError as exc:
        raise ValueError("Estado ou ação de análise inválidos") from exc
    destino = TRANSICOES_ANALISE.get((estado, acao_normalizada))
    if destino is None:
        raise ValueError(
            f"Transição não permitida: {estado.value} → {acao_normalizada.value}"
        )
    return destino


def revisao_obrigatoria_pendente(estado: str | EstadoAnalise) -> bool:
    try:
        return EstadoAnalise(estado) is not EstadoAnalise.VALIDATED
    except ValueError:
        return True

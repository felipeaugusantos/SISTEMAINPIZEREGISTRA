from enum import StrEnum


class StatusModelo(StrEnum):
    SHADOW = "SHADOW"
    VALIDATION = "VALIDATION"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


STATUS_MODELO_VALIDOS = tuple(status.value for status in StatusModelo)


def normalizar_status_modelo(status: str | None) -> StatusModelo:
    legado = {
        "candidato": StatusModelo.SHADOW,
        "reprovado": StatusModelo.DISABLED,
        "ativo": StatusModelo.ACTIVE,
        "arquivado": StatusModelo.DISABLED,
    }
    if status in legado:
        return legado[status]
    try:
        return StatusModelo(status or StatusModelo.DISABLED)
    except ValueError:
        return StatusModelo.DISABLED

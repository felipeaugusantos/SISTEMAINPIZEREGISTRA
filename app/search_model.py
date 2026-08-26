"""Contratos de publicação segura para ranking e busca avançada."""

from enum import StrEnum
from typing import Any


class StatusModeloBusca(StrEnum):
    SHADOW = "SHADOW"
    VALIDATION = "VALIDATION"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


STATUS_MODELO_BUSCA_VALIDOS = tuple(item.value for item in StatusModeloBusca)


def gate_publicacao_busca(
    atual: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    revisoes_humanas: int = 0,
) -> dict[str, Any]:
    """Bloqueia qualquer queda de recall e falsos negativos críticos."""
    falhas: list[str] = []
    if baseline:
        for k in (5, 10, 20):
            chave = f"recall_at_{k}"
            if float(atual.get(chave, 0)) < float(baseline.get(chave, 0)):
                falhas.append(f"{chave} regrediu")
        if int(atual.get("falsos_negativos_criticos", atual.get("falso_negativo_critico", 0))) > int(
            baseline.get("falsos_negativos_criticos", baseline.get("falso_negativo_critico", 0))
        ):
            falhas.append("falsos negativos críticos aumentaram")
    if int(atual.get("falsos_negativos_criticos", atual.get("falso_negativo_critico", 0))) > 0:
        falhas.append("existem falsos negativos críticos")
    if revisoes_humanas <= 0:
        falhas.append("não há validação humana registrada")
    return {"bloqueado": bool(falhas), "regressoes": falhas, "publicacao_permitida": not falhas}


def validar_transicao_status(atual: str, novo: str) -> None:
    StatusModeloBusca(atual)
    StatusModeloBusca(novo)
    permitidas = {
        ("SHADOW", "VALIDATION"),
        ("VALIDATION", "ACTIVE"),
        ("SHADOW", "DISABLED"),
        ("VALIDATION", "DISABLED"),
        ("ACTIVE", "DISABLED"),
        ("DISABLED", "SHADOW"),
    }
    if atual != novo and (atual, novo) not in permitidas:
        raise ValueError(f"Transição de modelo não permitida: {atual} → {novo}")


def resultado_busca_exige_revisao_humana() -> dict[str, Any]:
    return {
        "revisao_humana_obrigatoria": True,
        "natureza": "triagem_tecnica_de_anterioridades",
        "parecer_juridico_definitivo": False,
    }

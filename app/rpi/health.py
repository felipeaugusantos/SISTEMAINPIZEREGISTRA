from datetime import UTC, datetime

STATUS_ATIVOS = frozenset({"solicitada", "verificando", "importando"})


def avaliar_saude_rpi(
    *,
    status_sync: str | None,
    ultima_rpi_oficial: int | None,
    ultima_rpi_importada: int | None,
    ultima_sincronizacao: datetime | None,
    status_integridade: str | None,
    limite_atraso_horas: float,
    agora: datetime | None = None,
) -> tuple[str, float | None, list[str]]:
    agora = agora or datetime.now(UTC)
    motivos: list[str] = []
    idade_horas = None
    if ultima_sincronizacao is not None:
        referencia = ultima_sincronizacao
        if referencia.tzinfo is None:
            referencia = referencia.replace(tzinfo=UTC)
        idade_horas = max(0.0, (agora - referencia).total_seconds() / 3600)

    if status_sync in STATUS_ATIVOS:
        return "processando", idade_horas, motivos
    if status_sync == "falhou" or status_integridade == "erro":
        motivos.append("A última sincronização ou validação de integridade falhou.")
        return "erro", idade_horas, motivos
    if ultima_rpi_importada is None or ultima_sincronizacao is None:
        motivos.append("Ainda não existe importação de marca comprovada.")
        return "erro", idade_horas, motivos
    if ultima_rpi_oficial is not None and ultima_rpi_importada < ultima_rpi_oficial:
        motivos.append("A edição oficial mais recente ainda não foi importada.")
    if idade_horas is not None and idade_horas > limite_atraso_horas:
        motivos.append(
            f"A última importação tem {idade_horas:.1f} horas; limite de {limite_atraso_horas:.1f}."
        )
    if motivos:
        return "atrasado", idade_horas, motivos
    return "ok", idade_horas, motivos

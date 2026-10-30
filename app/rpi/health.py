from datetime import UTC, datetime, timedelta

STATUS_ATIVOS = frozenset({"solicitada", "verificando", "importando"})


def _utc(valor: datetime) -> datetime:
    return valor.astimezone(UTC) if valor.tzinfo else valor.replace(tzinfo=UTC)


def avaliar_saude_rpi(
    *,
    status_sync: str | None,
    ultima_rpi_oficial: int | None,
    ultima_rpi_importada: int | None,
    ultima_sincronizacao: datetime | None,
    status_integridade: str | None,
    limite_atraso_horas: float,
    proxima_verificacao_em: datetime | None = None,
    heartbeat_em: datetime | None = None,
    limite_heartbeat_segundos: float = 60,
    agora: datetime | None = None,
) -> tuple[str, float | None, list[str]]:
    agora = _utc(agora or datetime.now(UTC))
    motivos: list[str] = []
    idade_horas = None
    if ultima_sincronizacao is not None:
        referencia = _utc(ultima_sincronizacao)
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
    # A agenda semanal não exige nova consulta a cada 12 horas. Só confia
    # nela após uma verificação bem-sucedida e com o processo ainda ativo.
    # Datas ausentes/inválidas e heartbeat vencido mantêm a regra anterior.
    proxima = _utc(proxima_verificacao_em) if proxima_verificacao_em else None
    heartbeat_recente = heartbeat_em is not None and (
        0 <= (agora - _utc(heartbeat_em)).total_seconds() <= limite_heartbeat_segundos
    )
    agenda_valida = (
        status_sync == "atualizado"
        and proxima is not None
        and _utc(ultima_sincronizacao) < proxima <= _utc(ultima_sincronizacao) + timedelta(days=8)
        and heartbeat_recente
    )
    if agenda_valida:
        if agora > proxima + timedelta(hours=limite_atraso_horas):
            motivos.append("A verificação agendada da RPI ultrapassou o prazo de tolerância.")
    elif idade_horas is not None and idade_horas > limite_atraso_horas:
        motivos.append(f"A última verificação tem {idade_horas:.1f} horas; limite de {limite_atraso_horas:.1f}.")
    if motivos:
        return "atrasado", idade_horas, motivos
    return "ok", idade_horas, motivos

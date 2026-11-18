"""Disponibilidade do mês e histórico de incidentes (painel de TV).

Pedido do dono (17/11/2026): mostrar na TV a disponibilidade do mês e a lista
de incidentes (quedas e quanto duraram). Como o histórico só passa a existir a
partir da coleta, ela começou antes do painel: uma sonda no host da VPS
(docker/sonda-disponibilidade.sh, cron a cada minuto) grava uma linha por
alvo em ``backups/disponibilidade.jsonl`` e um fechamento diário em
``backups/disponibilidade-diario.jsonl`` (1 linha por dia por alvo, fuso de
Brasília). O worker lê os dois (diretório montado somente leitura), calcula o
mês e os incidentes e publica tudo no mesmo instantâneo do Redis que a API lê.

Regras (combinadas com o dono):

- minuto "fora" = sonda falhou (HTTP errado/timeout) OU não há linha nenhuma
  no minuto (a VPS reinicia sozinha às vezes: sem linha o app também estava
  fora para o usuário, então conta como fora, tipo "sem dados");
- incidente = 2+ minutos consecutivos fora; encerra com 2 minutos ok. Queda de
  1 minuto entra na disponibilidade mas não vira incidente listado;
- disponibilidade do mês = minutos no ar / minutos decorridos do mês (fuso de
  Brasília), começando no dia 1 ou no início da coleta, o que for mais
  recente. O painel mostra "desde <data>" enquanto o mês não está coberto;
- o raw guarda só os últimos 14 dias (detalhe de incidentes); o agregado
  diário fica para sempre (é ele que dá o histórico de mês fechado). Um dia
  coberto sem linha diária nem raw conta como 100% fora (sem dados,
  conservador e honesto); dia dentro da janela do raw mas sem fechamento
  (o cron fecha à noite) é calculado do próprio raw.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
ARQUIVO_RAW = "disponibilidade.jsonl"
ARQUIVO_DIARIO = "disponibilidade-diario.jsonl"
ALVOS = ("zeregistra", "erp")
MINUTOS_DIA = 1440
LIMIAR_INCIDENTE_MINUTOS = 2
ENCERRA_APOS_OK_MINUTOS = 2
INCIDENTES_MAX = 10
SAUDE_VERDE = 0.999
SAUDE_AMARELO = 0.99


def _parse_data(valor: object) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return data if data.tzinfo else data.replace(tzinfo=UTC)


def _ler_linhas(diretorio: Path, nome: str) -> list[dict]:
    try:
        linhas = (diretorio / nome).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    eventos: list[dict] = []
    for linha in linhas:
        try:
            evento = json.loads(linha)
        except ValueError:
            continue
        if isinstance(evento, dict):
            eventos.append(evento)
    return eventos


def _minuto(data: datetime) -> datetime:
    return data.astimezone(UTC).replace(second=0, microsecond=0)


def _sondas_raw(diretorio: Path, alvo: str) -> dict[datetime, bool]:
    """Minuto (UTC) -> no ar, só do raw (últimos 14 dias). Um ok no minuto basta."""
    minutos: dict[datetime, bool] = {}
    for evento in _ler_linhas(diretorio, ARQUIVO_RAW):
        if evento.get("alvo") != alvo:
            continue
        data = _parse_data(evento.get("em"))
        if data is None:
            continue
        chave = _minuto(data)
        minutos[chave] = minutos.get(chave, False) or bool(evento.get("ok"))
    return minutos


def _dias_agregados(diretorio: Path, alvo: str) -> dict[date, int]:
    """Dia (fuso de Brasília) -> minutos no ar, do fechamento diário."""
    dias: dict[date, int] = {}
    for evento in _ler_linhas(diretorio, ARQUIVO_DIARIO):
        if evento.get("alvo") != alvo:
            continue
        try:
            dia = date.fromisoformat(str(evento.get("dia")))
            up = int(evento.get("minutos_up"))
        except (TypeError, ValueError):
            continue
        dias[dia] = max(0, min(up, MINUTOS_DIA))
    return dias


def _incidentes(minutos: dict[datetime, bool], agora: datetime) -> tuple[list[dict], bool]:
    """Runs de minutos fora (sonda falhou ou minuto sem linha) com 2+ minutos.

    Um minuto ok solto no meio não encerra o incidente (entra na duração); o
    fim é o último minuto fora antes de ENCERRA_APOS_OK_MINUTOS minutos ok
    seguidos. Minutos sem linha até agora também contam (sonda parada = VPS
    fora = app fora)."""
    if not minutos:
        return [], False
    primeiro = min(minutos)
    # A janela fecha no minuto anterior: a linha do minuto corrente pode ainda
    # não ter sido gravada pela sonda e viraria falso "sem dados".
    fim_janela = max(max(minutos), _minuto(agora) - timedelta(minutes=1))
    incidentes: list[dict] = []
    inicio_run: datetime | None = None
    ultimo_fora: datetime | None = None
    run_falhou = False
    ok_seguidos = 0

    def registrar(fim: datetime | None) -> None:
        if inicio_run is None or ultimo_fora is None:
            return
        duracao = int(((ultimo_fora + timedelta(minutes=1)) - inicio_run).total_seconds() // 60)
        if duracao >= LIMIAR_INCIDENTE_MINUTOS:
            incidentes.append(
                {
                    "inicio": inicio_run,
                    "fim": fim,
                    "minutos": duracao,
                    "tipo": "falha" if run_falhou else "sem_dados",
                }
            )

    cursor = primeiro
    while cursor <= fim_janela:
        estado = minutos.get(cursor)
        if estado is True:
            if inicio_run is not None:
                ok_seguidos += 1
                if ok_seguidos >= ENCERRA_APOS_OK_MINUTOS:
                    registrar(ultimo_fora + timedelta(minutes=1))
                    inicio_run = None
                    run_falhou = False
                    ok_seguidos = 0
        else:
            # False = sonda falhou; None = sem linha (VPS fora/reinício)
            if inicio_run is None:
                inicio_run = cursor
                run_falhou = False
            run_falhou = run_falhou or estado is False
            ultimo_fora = cursor
            ok_seguidos = 0
        cursor += timedelta(minutes=1)

    aberto = False
    if inicio_run is not None and ultimo_fora is not None:
        duracao = int(((ultimo_fora + timedelta(minutes=1)) - inicio_run).total_seconds() // 60)
        if duracao >= LIMIAR_INCIDENTE_MINUTOS:
            incidentes.append(
                {
                    "inicio": inicio_run,
                    "fim": None,
                    "minutos": duracao,
                    "tipo": "falha" if run_falhou else "sem_dados",
                }
            )
            aberto = True
    incidentes.sort(key=lambda item: item["inicio"], reverse=True)
    return incidentes[:INCIDENTES_MAX], aberto


def _mes_do_alvo(agora: datetime, dias: dict[date, int], minutos: dict[datetime, bool]) -> dict:
    """Minutos no ar e decorridos do mês corrente (fuso de Brasília) de um alvo.

    Dias fechados vêm do agregado diário; dia recente ainda sem fechamento e o
    dia de hoje vêm do raw. O que antecede o primeiro dado conhecido não entra
    na conta (mês parcial desde o início da coleta)."""
    hoje = agora.astimezone(FUSO).date()
    primeiro_dia = hoje.replace(day=1)
    inicio_coleta: datetime | None = None
    if dias:
        inicio_coleta = datetime.combine(min(dias), time.min, tzinfo=FUSO).astimezone(UTC)
    if minutos:
        primeiro_raw = min(minutos)
        # O fechamento diário não guarda a hora de início: quando o raw alcança o
        # primeiro dia agregado, é ele quem diz quando a coleta começou de fato.
        if inicio_coleta is None or primeiro_raw.astimezone(FUSO).date() <= inicio_coleta.astimezone(FUSO).date():
            inicio_coleta = primeiro_raw
    if inicio_coleta is None:
        return {"disponivel": False}
    inicio_coleta_dia = inicio_coleta.astimezone(FUSO).date()

    up_raw_por_dia: dict[date, int] = {}
    for chave, ok in minutos.items():
        if ok:
            dia_sp = chave.astimezone(FUSO).date()
            up_raw_por_dia[dia_sp] = up_raw_por_dia.get(dia_sp, 0) + 1

    base = 0
    up_total = 0
    dia = primeiro_dia
    while dia < hoje:
        if dia >= inicio_coleta_dia:
            if dia in dias:
                up_total += dias[dia]
            elif dia in up_raw_por_dia:
                up_total += up_raw_por_dia[dia]  # fechamento da noite ainda não rodou
            # sem agregado e fora da janela do raw = dia coberto sem dado: 0 no ar
            if dia == inicio_coleta_dia:
                # Primeiro dia da coleta: conta só do início real até meia-noite,
                # nunca o dia inteiro (o que antecede a coleta não é "fora").
                ini_sp = inicio_coleta.astimezone(FUSO)
                base += MINUTOS_DIA - (ini_sp.hour * 60 + ini_sp.minute)
            else:
                base += MINUTOS_DIA
        dia += timedelta(days=1)

    # Hoje, minuto a minuto, do raw: falta de linha conta como fora. Fecha no
    # minuto anterior (a linha do minuto corrente pode ainda não existir).
    inicio_hoje = datetime.combine(hoje, time.min, tzinfo=FUSO).astimezone(UTC)
    cursor = max(_minuto(inicio_hoje), _minuto(inicio_coleta))
    fim = _minuto(agora) - timedelta(minutes=1)
    while cursor <= fim:
        base += 1
        if minutos.get(cursor) is True:
            up_total += 1
        cursor += timedelta(minutes=1)

    return {
        "disponivel": base > 0,
        # Nunca antes do dia 1: mês inteiro coberto não é "parcial".
        "coletando_desde": max(inicio_coleta_dia, primeiro_dia).isoformat(),
        "minutos_mes": base,
        "minutos_up_mes": up_total,
        "minutos_fora_mes": base - up_total,
        "disponibilidade_mes": up_total / base if base else None,
    }


def _saude(resumo: dict, incidente_aberto: bool) -> str:
    """Incidente aberto = vermelho; senão a cor segue a disponibilidade do mês."""
    if not resumo.get("disponivel"):
        return "cinza"
    if incidente_aberto:
        return "vermelho"
    pct = resumo.get("disponibilidade_mes")
    if pct is None:
        return "cinza"
    if pct >= SAUDE_VERDE:
        return "verde"
    if pct >= SAUDE_AMARELO:
        return "amarelo"
    return "vermelho"


def coletar_disponibilidade(diretorio: Path, *, agora: datetime | None = None) -> dict:
    """Lê raw + fechamento diário de ``diretorio`` (backups/) e calcula o mês e os incidentes."""
    agora = agora or datetime.now(UTC)
    alvos: dict[str, dict] = {}
    incidentes: list[dict] = []
    for alvo in ALVOS:
        minutos = _sondas_raw(diretorio, alvo)
        lista, aberto = _incidentes(minutos, agora)
        resumo = _mes_do_alvo(agora, _dias_agregados(diretorio, alvo), minutos)
        resumo["incidente_aberto"] = aberto
        resumo["saude"] = _saude(resumo, aberto)
        alvos[alvo] = resumo
        for item in lista:
            incidentes.append(
                {
                    "alvo": alvo,
                    "inicio": item["inicio"].isoformat(),
                    "fim": item["fim"].isoformat() if item["fim"] else None,
                    "minutos": item["minutos"],
                    "tipo": item["tipo"],
                }
            )
    incidentes.sort(key=lambda item: item["inicio"], reverse=True)
    hoje = agora.astimezone(FUSO).date()
    return {
        "coletado_em": agora.isoformat(),
        "mes": f"{hoje.year:04d}-{hoje.month:02d}",
        "alvos": alvos,
        "incidentes": incidentes[:INCIDENTES_MAX],
    }

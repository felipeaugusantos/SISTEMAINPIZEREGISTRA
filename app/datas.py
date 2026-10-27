"""Datas no fuso de negócio (Brasil).

`date.today()` usa o fuso do servidor, que em Docker costuma ser UTC: a partir
das 21h no Brasil "hoje" passaria a ser o dia seguinte (parcela vencendo hoje
aparecendo como atrasada, data de baixa errada). Use `hoje_brasil()` em regra
de negócio que compara ou grava datas de calendário.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")


def hoje_brasil() -> date:
    return datetime.now(UTC).astimezone(FUSO_BRASIL).date()

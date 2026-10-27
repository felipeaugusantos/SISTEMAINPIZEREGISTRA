from datetime import UTC, date, datetime
from unittest.mock import patch

from app import datas


def test_hoje_brasil_usa_fuso_de_sao_paulo_e_nao_o_utc() -> None:
    # 23h30 em Brasília (UTC-3) já é 02h30 do dia seguinte em UTC.
    class Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 28, 2, 30, tzinfo=UTC).astimezone(tz)

    with patch.object(datas, "datetime", Relogio):
        assert datas.hoje_brasil() == date(2026, 10, 27)

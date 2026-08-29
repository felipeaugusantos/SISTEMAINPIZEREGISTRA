from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.cli.atualizar_rpis_automaticamente import calcular_proxima_verificacao, intervalo_pendente
from app.rpi.latest import extrair_ultima_rpi

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")


def test_extrai_maior_numero_da_tabela_oficial() -> None:
    html = """
    <table>
      <tr><td>2900</td><td>04/08/2026</td></tr>
      <tr><td class="numero"> 2899 </td><td>28/07/2026</td></tr>
    </table>
    """

    assert extrair_ultima_rpi(html) == 2900


def test_falha_quando_pagina_nao_contem_rpi() -> None:
    with pytest.raises(ValueError, match="Nenhum número"):
        extrair_ultima_rpi("<html><body>indisponível</body></html>")


def test_identifica_lacuna_entre_edicoes_importadas() -> None:
    assert intervalo_pendente({2898, 2900}, 2898, 2900) == (2899, 2899)


def test_intervalo_vazio_quando_todas_edicoes_foram_importadas() -> None:
    assert intervalo_pendente({2898, 2899, 2900}, 2898, 2900) is None


def test_falha_na_terca_10h_reagenda_para_12h_do_mesmo_dia() -> None:
    agora = datetime(2026, 9, 1, 10, 5, tzinfo=FUSO_BRASILIA)  # terca-feira
    proxima = calcular_proxima_verificacao(agora, sucesso=False).astimezone(FUSO_BRASILIA)
    assert (proxima.date(), proxima.hour) == (agora.date(), 12)


def test_falha_na_terca_12h_reagenda_para_15h_do_mesmo_dia() -> None:
    agora = datetime(2026, 9, 1, 12, 5, tzinfo=FUSO_BRASILIA)
    proxima = calcular_proxima_verificacao(agora, sucesso=False).astimezone(FUSO_BRASILIA)
    assert (proxima.date(), proxima.hour) == (agora.date(), 15)


def test_falha_na_terca_15h_esgota_tentativas_do_dia_e_volta_a_proxima_terca() -> None:
    agora = datetime(2026, 9, 1, 15, 5, tzinfo=FUSO_BRASILIA)
    proxima = calcular_proxima_verificacao(agora, sucesso=False).astimezone(FUSO_BRASILIA)
    assert proxima.weekday() == 1
    assert proxima.hour == 10
    assert proxima.date() == datetime(2026, 9, 8).date()


def test_sucesso_sempre_reagenda_para_a_proxima_terca_as_10h() -> None:
    agora = datetime(2026, 9, 1, 10, 0, 1, tzinfo=FUSO_BRASILIA)
    proxima = calcular_proxima_verificacao(agora, sucesso=True).astimezone(FUSO_BRASILIA)
    assert proxima.weekday() == 1
    assert proxima.hour == 10
    assert proxima.date() == datetime(2026, 9, 8).date()


def test_falha_fora_de_terca_reagenda_para_a_proxima_terca() -> None:
    agora = datetime(2026, 9, 2, 14, 0, tzinfo=FUSO_BRASILIA)  # quarta-feira
    proxima = calcular_proxima_verificacao(agora, sucesso=False).astimezone(FUSO_BRASILIA)
    assert proxima.weekday() == 1
    assert proxima.hour == 10
    assert proxima.date() == datetime(2026, 9, 8).date()

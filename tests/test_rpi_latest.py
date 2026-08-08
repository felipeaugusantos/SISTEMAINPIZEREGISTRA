import pytest

from app.cli.atualizar_rpis_automaticamente import intervalo_pendente
from app.rpi.latest import extrair_ultima_rpi


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

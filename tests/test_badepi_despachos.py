from datetime import date
from pathlib import Path

import pytest

from app.badepi.despachos import _chave_origem, ler_despachos_marcas
from app.badepi.despachos_codigos import codigo_numerico, descricao_despacho


def test_codigo_numerico_normaliza_prefixos() -> None:
    assert codigo_numerico("DESP009") == "009"
    assert codigo_numerico("IPAS009") == "009"
    assert codigo_numerico("DESP1073") == "1073"
    assert codigo_numerico("") is None
    assert codigo_numerico(None) is None


def test_descricao_despacho_mapeia_por_numero() -> None:
    assert descricao_despacho("DESP009") == ("Publicação de pedido de registro para oposição (exame formal concluído)")
    assert descricao_despacho("IPAS024") == "Indeferimento do pedido"
    assert descricao_despacho("DESP158") == "Concessão de registro"
    # código legado fora da tabela atual não inventa descrição
    assert descricao_despacho("DESP150") is None


def test_le_despachos_badepi(tmp_path: Path) -> None:
    arquivo = tmp_path / "despacho.csv"
    arquivo.write_text(
        '"NO_PEDIDO";"NO_RPI";"DT_PUBLICACAO";"CD_DESPACH_RPI"\n'
        '"822344092";"1521";"29/02/2000 00:00:00";"DESP158"\n'
        '"900000001";"2500";"01/03/2019 00:00:00";"DESP150"\n',
        encoding="cp1252",
    )

    registros = list(ler_despachos_marcas(arquivo))

    numero, codigo, descricao, data_rpi, numero_rpi, chave = registros[0]
    assert numero == "822344092"
    assert codigo == "DESP158"
    assert descricao == "Concessão de registro"
    assert data_rpi == date(2000, 2, 29)
    assert numero_rpi == 1521
    assert len(chave) == 64
    # código legado cai no rótulo com o próprio código (não classificável)
    assert registros[1][2] == "Despacho DESP150"


def test_ignora_linhas_sem_data_ou_rpi(tmp_path: Path) -> None:
    arquivo = tmp_path / "despacho.csv"
    arquivo.write_text(
        '"NO_PEDIDO";"NO_RPI";"DT_PUBLICACAO";"CD_DESPACH_RPI"\n'
        '"1";"";"29/02/2000 00:00:00";"DESP158"\n'
        '"2";"1521";"";"DESP158"\n'
        '"3";"1521";"29/02/2000 00:00:00";""\n',
        encoding="cp1252",
    )

    assert list(ler_despachos_marcas(arquivo)) == []


def test_rejeita_csv_sem_colunas(tmp_path: Path) -> None:
    arquivo = tmp_path / "invalido.csv"
    arquivo.write_text('"NO_PEDIDO"\n"1"\n', encoding="cp1252")

    with pytest.raises(ValueError, match="Colunas BADEPI ausentes"):
        list(ler_despachos_marcas(arquivo))


def test_chave_origem_determinista_e_distinta() -> None:
    a = _chave_origem("822344092", 1521, "DESP158")
    b = _chave_origem("822344092", 1521, "DESP158")
    c = _chave_origem("822344092", 1522, "DESP158")
    assert a == b
    assert a != c


def test_chave_origem_dedupe_por_codigo_numerico() -> None:
    # O BADEPI grava o mesmo despacho sob formas distintas; todas devem colapsar
    # na mesma chave para não duplicar a movimentação.
    numerico = _chave_origem("822344092", 1521, "009")
    ipas = _chave_origem("822344092", 1521, "IPAS009")
    desp = _chave_origem("822344092", 1521, "DESP009")
    assert numerico == ipas == desp
    # código diferente continua gerando chave distinta
    assert _chave_origem("822344092", 1521, "IPAS024") != numerico

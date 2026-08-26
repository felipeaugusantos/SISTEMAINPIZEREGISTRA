from pathlib import Path

import pytest

from app.badepi.marcas import ler_depositos_marcas


def test_le_deposito_badepi_cp1252(tmp_path: Path) -> None:
    arquivo = tmp_path / "depositos.csv"
    arquivo.write_text(
        '"NO_PEDIDO";"NM_TITULO_MARCA";"DT_DEPOSITO"\n"933404204";"COCA-COLA";"02/02/2024 15:27:54"\n',
        encoding="cp1252",
    )

    registros = list(ler_depositos_marcas(arquivo))

    assert registros[0][0] == "933404204"
    assert registros[0][1] == "COCA-COLA"
    assert registros[0][2].isoformat() == "2024-02-02"


def test_le_apresentacao_e_natureza(tmp_path: Path) -> None:
    arquivo = tmp_path / "depositos.csv"
    arquivo.write_text(
        '"NO_PEDIDO";"NM_TITULO_MARCA";"DT_DEPOSITO";'
        '"CD_APRESEN_MARCA";"DS_NATUREZ_MARCA"\n'
        '"1";"MISTA SA";"02/02/2024 15:27:54";"M";"Marca de Produto/Servico"\n'
        '"2";"SEM APRES";"02/02/2024 15:27:54";"O";""\n',
        encoding="cp1252",
    )

    registros = list(ler_depositos_marcas(arquivo))

    # M -> Mista, natureza vem da descrição
    assert registros[0][3] == "Mista"
    assert registros[0][4] == "Marca de Produto/Servico"
    # código desconhecido ('O') vira nulo em vez de apresentação inventada
    assert registros[1][3] is None
    assert registros[1][4] is None


def test_apresentacao_ausente_quando_coluna_nao_existe(tmp_path: Path) -> None:
    arquivo = tmp_path / "depositos.csv"
    arquivo.write_text(
        '"NO_PEDIDO";"NM_TITULO_MARCA";"DT_DEPOSITO"\n"933404204";"COCA-COLA";"02/02/2024 15:27:54"\n',
        encoding="cp1252",
    )

    registros = list(ler_depositos_marcas(arquivo))

    assert registros[0][3] is None
    assert registros[0][4] is None


def test_rejeita_csv_sem_colunas_obrigatorias(tmp_path: Path) -> None:
    arquivo = tmp_path / "invalido.csv"
    arquivo.write_text('"OUTRA_COLUNA"\n"valor"\n', encoding="cp1252")

    with pytest.raises(ValueError, match="Colunas BADEPI ausentes"):
        list(ler_depositos_marcas(arquivo))

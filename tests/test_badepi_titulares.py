from pathlib import Path

import pytest

from app.badepi.titulares import ler_titulares_marcas


def test_le_titular_badepi_cp1252(tmp_path: Path) -> None:
    arquivo = tmp_path / "titulares.csv"
    arquivo.write_text(
        '"NO_PEDIDO";"NM_COMPLET_PFPJ";"CD_PAIS_PFPJ"\n'
        '"933404204";"COCA-COLA INDÚSTRIAS LTDA";"BR"\n',
        encoding="cp1252",
    )

    assert list(ler_titulares_marcas(arquivo)) == [
        ("933404204", "COCA-COLA INDÚSTRIAS LTDA", "BR")
    ]


def test_rejeita_titulares_sem_colunas_obrigatorias(tmp_path: Path) -> None:
    arquivo = tmp_path / "invalido.csv"
    arquivo.write_text('"OUTRA_COLUNA"\n"valor"\n', encoding="cp1252")

    with pytest.raises(ValueError, match="Colunas BADEPI ausentes"):
        list(ler_titulares_marcas(arquivo))

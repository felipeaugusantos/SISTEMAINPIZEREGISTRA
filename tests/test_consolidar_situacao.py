from app.cli.consolidar_situacoes_marcas import _comando


def test_comando_individual_filtra_por_processo() -> None:
    sql = str(_comando(True))
    assert "AND p.id = :processo_id" in sql


def test_comando_global_sem_filtro_de_processo() -> None:
    sql = str(_comando(False))
    assert ":processo_id" not in sql
    # mantém o escopo de marca em ambos os modos
    assert "p.tipo = 'marca'" in sql

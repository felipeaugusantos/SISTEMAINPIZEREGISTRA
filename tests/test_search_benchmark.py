from app.cli.avaliar_busca_marcas import metricas_resultado


def test_metricas_do_benchmark_calculam_recall_e_precision() -> None:
    recall, precision = metricas_resultado(["1", "2", "3", "4"], {"2", "4", "5"})

    assert recall == 2 / 3
    assert precision == 1 / 2

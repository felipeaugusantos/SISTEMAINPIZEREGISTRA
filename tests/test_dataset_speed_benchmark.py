from app.cli.avaliar_velocidade_dataset import avaliar_regressao_velocidade


def _resultado(*, ms_por_rotulo: float) -> dict:
    return {
        "limite": 500,
        "candidatos_por_processo": 12,
        "rotulos_processados": 500,
        "pares_processados": 6000,
        "duracao_s": ms_por_rotulo * 500 / 1000,
        "ms_por_rotulo": ms_por_rotulo,
        "ms_por_par": ms_por_rotulo / 12,
    }


def test_gate_aceita_variacao_dentro_da_tolerancia() -> None:
    baseline = _resultado(ms_por_rotulo=100.0)
    atual = _resultado(ms_por_rotulo=140.0)

    assert avaliar_regressao_velocidade(atual, baseline, tolerancia=0.5) == []


def test_gate_bloqueia_regressao_alem_da_tolerancia() -> None:
    baseline = _resultado(ms_por_rotulo=100.0)
    atual = _resultado(ms_por_rotulo=200.0)

    falhas = avaliar_regressao_velocidade(atual, baseline, tolerancia=0.5)

    assert any("ms_por_rotulo regrediu" in falha for falha in falhas)


def test_gate_ignora_baseline_zerado() -> None:
    baseline = _resultado(ms_por_rotulo=0.0)
    atual = _resultado(ms_por_rotulo=500.0)

    assert avaliar_regressao_velocidade(atual, baseline, tolerancia=0.5) == []

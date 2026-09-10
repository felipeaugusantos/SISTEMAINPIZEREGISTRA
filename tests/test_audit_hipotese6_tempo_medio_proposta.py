"""Auditoria técnica (10/09/2026) — Hipótese 6: o indicador
"tempo_medio_ate_proposta_dias" usaria lead.atualizado_em - lead.criado_em,
sensível a qualquer edição posterior do lead?

Testa a função pura _tempo_medio_ate_proposta_dias (app/api/leads.py)
diretamente, sem HTTP nem banco -- mais preciso que simular a query
agregada via FakeSession.
"""

from datetime import UTC, datetime, timedelta

from app.api.leads import _tempo_medio_ate_proposta_dias
from app.models import Lead


def _lead_pos_proposta(**overrides: object) -> Lead:
    base = dict(
        id=1,
        organizacao_id=1,
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        origem="processo",
        fase="proposta_enviada",
    )
    base.update(overrides)
    return Lead(**base)


def test_com_historico_de_fase_edicao_posterior_nao_altera_o_indicador() -> None:
    """CONFIRMADO (achado L10, já corrigido -- ver docstring de
    _tempo_medio_ate_proposta_dias): quando existe um evento real em
    HistoricoFaseLead para a transição a "proposta_enviada", é ELE que é
    usado -- não lead.atualizado_em. Uma edição do lead bem depois (ex.:
    trocar responsável, adicionar nota) muda atualizado_em mas NÃO muda o
    indicador."""
    criado_em = datetime(2026, 1, 1, tzinfo=UTC)
    entrada_real_na_proposta = datetime(2026, 1, 5, tzinfo=UTC)  # 4 dias depois
    lead = _lead_pos_proposta(criado_em=criado_em)
    # Simula uma edição bem posterior (ex.: 40 dias depois) -- se o bug
    # existisse, o indicador refletiria isso; com o fix, é ignorado.
    lead.atualizado_em = datetime(2026, 2, 10, tzinfo=UTC)

    valores = _tempo_medio_ate_proposta_dias([lead], entradas_proposta={1: entrada_real_na_proposta})

    assert valores == [4.0]


def test_sem_historico_de_fase_o_fallback_ainda_usa_atualizado_em() -> None:
    """Achado residual (não é bug novo, é uma lacuna conhecida e explícita
    no próprio código: "quando não há esse histórico ... cai para
    atualizado_em em vez de descartar o lead da métrica"). Para um lead
    cuja fase chegou a "proposta_enviada" por um caminho que não passou
    por HistoricoFaseLead (sem entrada em entradas_proposta), o indicador
    ainda usa atualizado_em -- ou seja, o comportamento antigo (sensível a
    qualquer edição) SOBREVIVE nesse caso específico. Este teste documenta
    o fallback, não é uma falha do teste."""
    criado_em = datetime(2026, 1, 1, tzinfo=UTC)
    lead = _lead_pos_proposta(criado_em=criado_em)
    lead.atualizado_em = datetime(2026, 1, 3, tzinfo=UTC)  # 2 dias depois

    valores_antes = _tempo_medio_ate_proposta_dias([lead], entradas_proposta={})
    assert valores_antes == [2.0]

    # Uma edição não relacionada à proposta (ex.: mudar uma tag) 10 dias
    # depois desloca o indicador, porque não há entradas_proposta[1] para
    # âncorar o cálculo em um evento real.
    lead.atualizado_em = datetime(2026, 1, 13, tzinfo=UTC)
    valores_depois = _tempo_medio_ate_proposta_dias([lead], entradas_proposta={})
    assert valores_depois == [12.0]
    assert valores_depois != valores_antes


def test_lead_fora_das_fases_pos_proposta_nao_entra_na_media() -> None:
    lead = _lead_pos_proposta(fase="qualificado", criado_em=datetime.now(UTC) - timedelta(days=5))
    assert _tempo_medio_ate_proposta_dias([lead], entradas_proposta={}) == []

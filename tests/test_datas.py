"""Dia de negócio em Brasília, independente do fuso do servidor.

A virada é entre 02h59 e 03h00 UTC (Brasília = UTC-3, sem horário de verão):
até 02h59 UTC o dia de Brasília ainda é o anterior ao do UTC.
"""

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from app import datas
from app.api.contratacoes import ContratacaoInput, contratar_servico
from app.api.financeiro import _serializar, pagar_comissao
from app.api.leads_propostas import motivo_bloqueio_aceite
from app.api.portal_cliente import _serializar_parcela_portal
from app.models import (
    ComissaoFinanceira,
    ContratacaoServico,
    LancamentoFinanceiro,
    ParcelaFinanceira,
    PropostaComercial,
    ServicoFinanceiro,
)
from tests.conftest import FakeResult, FakeSession, usuario_teste


def _relogio(instante_utc: datetime):
    class Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            return instante_utc.astimezone(tz) if tz else instante_utc

    return patch.object(datas, "datetime", Relogio)


# UTC já virou para 28/10, Brasília ainda está em 27/10.
ANTES_DA_VIRADA = datetime(2026, 10, 28, 2, 59, 59, tzinfo=UTC)
NA_VIRADA = datetime(2026, 10, 28, 3, 0, 0, tzinfo=UTC)
NOITE_BRASIL = datetime(2026, 10, 28, 1, 30, tzinfo=UTC)  # 22h30 de 27/10 em Brasília


@pytest.mark.parametrize(
    ("instante", "esperado"),
    [
        (datetime(2026, 10, 27, 12, 0, tzinfo=UTC), date(2026, 10, 27)),
        (datetime(2026, 10, 27, 23, 59, 59, tzinfo=UTC), date(2026, 10, 27)),
        (NOITE_BRASIL, date(2026, 10, 27)),
        (ANTES_DA_VIRADA, date(2026, 10, 27)),
        (NA_VIRADA, date(2026, 10, 28)),
        (datetime(2026, 10, 28, 12, 0, tzinfo=UTC), date(2026, 10, 28)),
    ],
)
def test_hoje_brasil_acompanha_a_meia_noite_de_brasilia(instante: datetime, esperado: date) -> None:
    with _relogio(instante):
        assert datas.hoje_brasil() == esperado


def test_hoje_brasil_nao_depende_do_fuso_do_processo(monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset indisponível nesta plataforma")
    resultados = set()
    try:
        for fuso in ("UTC", "Asia/Tokyo", "America/Sao_Paulo", "Pacific/Auckland"):
            monkeypatch.setenv("TZ", fuso)
            time.tzset()
            with _relogio(ANTES_DA_VIRADA):
                resultados.add(datas.hoje_brasil())
    finally:
        monkeypatch.undo()
        time.tzset()
    assert resultados == {date(2026, 10, 27)}


# --- Efeitos nas regras de negócio, com UTC já em 28/10 e Brasília ainda em 27/10 ---


def _lancamento_com_parcela(vencimento: date) -> LancamentoFinanceiro:
    lancamento = LancamentoFinanceiro(
        id=1, organizacao_id=1, tipo="receber", descricao="x", valor_total=Decimal("100"), status="aberto"
    )
    lancamento.parcelas = [
        ParcelaFinanceira(
            id=1, organizacao_id=1, lancamento_id=1, numero=1, vencimento=vencimento, valor=Decimal("100"), status="aberta"
        )
    ]
    return lancamento


def test_parcela_que_vence_hoje_em_brasilia_nao_aparece_atrasada_no_financeiro() -> None:
    lancamento = _lancamento_com_parcela(date(2026, 10, 27))
    with _relogio(ANTES_DA_VIRADA):
        assert _serializar(lancamento)["parcelas"][0]["status"] == "aberta"
    with _relogio(NA_VIRADA):
        assert _serializar(lancamento)["parcelas"][0]["status"] == "atrasada"


def test_parcela_que_vence_hoje_em_brasilia_nao_aparece_atrasada_no_portal() -> None:
    parcela = ParcelaFinanceira(
        id=1, organizacao_id=1, lancamento_id=1, numero=1, vencimento=date(2026, 10, 27), valor=Decimal("100"), status="aberta"
    )
    with _relogio(ANTES_DA_VIRADA):
        assert _serializar_parcela_portal(parcela, {})["status"] == "aberta"
    with _relogio(NA_VIRADA):
        assert _serializar_parcela_portal(parcela, {})["status"] == "atrasada"


def test_proposta_valida_ate_hoje_em_brasilia_ainda_aceita_apos_virada_do_utc() -> None:
    proposta = PropostaComercial(id=1, organizacao_id=1, numero="P-1", status="enviada", validade_em=date(2026, 10, 27))
    with _relogio(ANTES_DA_VIRADA):
        assert motivo_bloqueio_aceite(proposta) is None
    with _relogio(NA_VIRADA):
        assert "expirou" in (motivo_bloqueio_aceite(proposta) or "")


def test_pagar_comissao_grava_a_data_de_brasilia() -> None:
    comissao = ComissaoFinanceira(
        id=1, organizacao_id=1, usuario_id=7, lancamento_id=1, parcela_id=10,
        valor_base=Decimal("1000"), percentual=Decimal("10"), valor_comissao=Decimal("100"), status="pendente",
    )
    session = FakeSession([FakeResult(scalar=comissao)])
    request = _request()
    with _relogio(ANTES_DA_VIRADA):
        asyncio.run(pagar_comissao(1, request, session, usuario_teste()))
    assert comissao.status == "paga"
    assert comissao.pago_em == date(2026, 10, 27)


def test_contratacao_usa_a_data_de_brasilia_na_competencia_e_no_primeiro_vencimento() -> None:
    servico = ServicoFinanceiro(
        id=1, organizacao_id=1, codigo="REG", nome="Registro", valor=Decimal("300"), ativo=True
    )
    session = FakeSession(
        [FakeResult(scalar=None), FakeResult(scalar=1), FakeResult(scalar=servico)]
    )
    with _relogio(ANTES_DA_VIRADA):
        asyncio.run(
            contratar_servico(
                ContratacaoInput(servico_id=1, lead_id=9, conta_contabil_id=1, parcelas=2, idempotency_key="chave-virada-dia"),
                session,
                usuario_teste(),
            )
        )
    lancamento = next(o for o in session.adicionados if isinstance(o, LancamentoFinanceiro))
    parcelas = [o for o in session.adicionados if isinstance(o, ParcelaFinanceira)]
    assert lancamento.competencia == date(2026, 10, 27)
    assert parcelas[0].vencimento == date(2026, 10, 27)
    assert parcelas[1].vencimento == date(2026, 10, 27) + timedelta(days=30)
    assert any(isinstance(o, ContratacaoServico) for o in session.adicionados)


def _request():
    from starlette.requests import Request

    return Request({"type": "http", "method": "POST", "headers": [], "client": ("127.0.0.1", 50000)})

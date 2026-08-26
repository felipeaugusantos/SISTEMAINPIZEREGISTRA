import asyncio
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from app.api.financeiro import LancamentoFinanceiro, ParcelaFinanceira
from app.api.juridico import PrazoUpdate, atualizar_prazo
from app.crm import aplicar_politica_oportunidade, aplicar_regras_automacao
from app.models import EventoDominio, Lead, LembreteCRM, PoliticaCRM, PrazoJuridico, StatusLead
from tests.conftest import FakeResult, FakeSession, usuario_teste


class IterableFakeResult(FakeResult):
    def __iter__(self):
        return iter(self._itens)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/v1/admin/juridico/prazos/1",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _lead() -> Lead:
    return Lead(
        id=10,
        organizacao_id=1,
        nome="Cliente",
        email="cliente@example.test",
        telefone="11999999999",
        marca="Marca",
        status=StatusLead.NOVO,
        fase="contato_inicial",
    )


def test_politica_configuravel_atribui_operador_e_proxima_acao() -> None:
    lead = _lead()
    politica = PoliticaCRM(
        organizacao_id=1,
        exigir_responsavel=True,
        atribuir_ao_operador=True,
        exigir_proxima_acao=True,
        dias_proxima_acao_padrao=2,
    )
    session = FakeSession([FakeResult(scalar=politica)])

    faltando = asyncio.run(aplicar_politica_oportunidade(session, lead, operador_id=7))

    assert faltando == []
    assert lead.responsavel_id == 7
    assert lead.proxima_acao_em is not None


def test_automacao_reprocessada_nao_duplica_lembrete() -> None:
    lead = _lead()
    primeira = FakeSession([IterableFakeResult(itens=[]), FakeResult(scalar=None)])
    segunda = FakeSession([IterableFakeResult(itens=[]), FakeResult(scalar=123)])

    assert asyncio.run(aplicar_regras_automacao(primeira, lead, "status", "sem_retorno", "teste")) == [
        "reengajar_sem_retorno"
    ]
    assert asyncio.run(aplicar_regras_automacao(segunda, lead, "status", "sem_retorno", "teste")) == []
    assert len([x for x in primeira.adicionados if isinstance(x, LembreteCRM)]) == 1
    assert len([x for x in primeira.adicionados if isinstance(x, EventoDominio)]) == 1
    assert not [x for x in segunda.adicionados if isinstance(x, LembreteCRM)]


def test_confirmacao_juridica_exige_responsavel_e_justificativa() -> None:
    prazo = PrazoJuridico(
        id=1,
        organizacao_id=1,
        processo_monitorado_id=2,
        titulo="Responder exigência",
        tipo="exigencia",
        origem="motor_rpi",
        data_base=date(2026, 8, 14),
        dias_prazo=60,
        contagem="corridos",
        vencimento_em=datetime(2026, 10, 13, tzinfo=UTC),
        status="aguardando_confirmacao",
        prioridade="alta",
        confirmado=False,
        criado_por="motor-juridico",
    )
    session = FakeSession([FakeResult(scalar=prazo)])
    with pytest.raises(HTTPException) as erro:
        asyncio.run(atualizar_prazo(1, PrazoUpdate(confirmar=True), _request(), session, usuario_teste()))
    assert erro.value.status_code == 422


def test_confirmacao_juridica_guarda_trilha_completa() -> None:
    prazo = PrazoJuridico(
        id=1,
        organizacao_id=1,
        processo_monitorado_id=2,
        responsavel_id=1,
        titulo="Responder exigência",
        tipo="exigencia",
        origem="motor_rpi",
        data_base=date(2026, 8, 14),
        dias_prazo=60,
        contagem="corridos",
        vencimento_em=datetime(2026, 10, 13, tzinfo=UTC),
        status="aguardando_confirmacao",
        prioridade="alta",
        confirmado=False,
        criado_por="motor-juridico",
    )
    session = FakeSession([FakeResult(scalar=prazo)])
    asyncio.run(
        atualizar_prazo(
            1,
            PrazoUpdate(confirmar=True, confirmacao_observacoes="RPI e data conferidas."),
            _request(),
            session,
            usuario_teste(),
        )
    )
    assert prazo.confirmado_por_id == 1
    assert prazo.confirmado_em is not None
    assert prazo.confirmacao_origem == "revisao_humana"
    assert prazo.confirmacao_observacoes == "RPI e data conferidas."


def test_operacoes_financeiras_usam_lock_do_lancamento_e_parcela() -> None:
    consulta = (
        select(ParcelaFinanceira)
        .join(LancamentoFinanceiro, LancamentoFinanceiro.id == ParcelaFinanceira.lancamento_id)
        .with_for_update(of=(LancamentoFinanceiro, ParcelaFinanceira))
    )
    sql = str(consulta.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF lancamentos_financeiros, parcelas_financeiras" in sql

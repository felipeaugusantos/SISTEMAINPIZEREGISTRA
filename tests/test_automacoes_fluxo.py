from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.crm import _chave_lembrete_fluxo, _estado_regra_fluxo, reconciliar_automacoes_fluxo_contratacao
from app.models import EventoDominio
from tests.conftest import FakeResult, FakeSession


def _proposta(**mudancas):
    inicio = datetime(2026, 9, 1, tzinfo=UTC)
    dados = {
        "id": 20,
        "organizacao_id": 1,
        "lead_id": 10,
        "status": "aceita",
        "aceito_em": inicio,
        "pagamento_status": "pendente",
        "pagamento_confirmado_em": None,
        "juridico_recebido_em": None,
        "sla_inicio_em": None,
        "sla_prazo_em": None,
        "protocolo_em": None,
        "responsavel_protocolo_id": None,
    }
    dados.update(mudancas)
    return SimpleNamespace(**dados)


def _lead():
    return SimpleNamespace(id=10, organizacao_id=1, responsavel_id=7, arquivado_em=None)


def test_estado_das_regras_respeita_as_passagens_reais() -> None:
    inicio = datetime(2026, 9, 1, tzinfo=UTC)
    proposta = _proposta()
    assert _estado_regra_fluxo(proposta, "cobrar_pagamento") == (True, inicio)

    proposta.pagamento_status = "confirmado"
    proposta.pagamento_confirmado_em = inicio + timedelta(days=1)
    assert _estado_regra_fluxo(proposta, "cobrar_pagamento")[0] is False
    assert _estado_regra_fluxo(proposta, "receber_juridico")[0] is True

    proposta.juridico_recebido_em = inicio + timedelta(days=2)
    proposta.sla_inicio_em = inicio + timedelta(days=2)
    proposta.sla_prazo_em = inicio + timedelta(days=3)
    assert _estado_regra_fluxo(proposta, "receber_juridico")[0] is False
    assert _estado_regra_fluxo(proposta, "protocolar_no_prazo") == (True, proposta.sla_prazo_em)


def test_cobranca_reutiliza_chave_do_gatilho_em_tempo_real() -> None:
    assert _chave_lembrete_fluxo(_proposta(), "cobrar_pagamento") == (
        "lead:10:fase:proposta_aceita:cobrar_pagamento"
    )


@pytest.mark.asyncio
async def test_reconciliacao_cria_apenas_a_tarefa_da_etapa_atual() -> None:
    proposta = _proposta()
    session = FakeSession(
        [
            FakeResult(itens=[(proposta, _lead())]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(scalar=101),
        ]
    )

    resultado = await reconciliar_automacoes_fluxo_contratacao(session)

    assert resultado == {"criados": 1, "concluidos": 0, "propostas_avaliadas": 1}
    eventos = [item for item in session.adicionados if isinstance(item, EventoDominio)]
    assert len(eventos) == 1
    assert eventos[0].payload["regra"] == "cobrar_pagamento"


@pytest.mark.asyncio
async def test_reconciliacao_conclui_lembrete_obsoleto_sem_duplicar() -> None:
    inicio = datetime(2026, 9, 1, tzinfo=UTC)
    proposta = _proposta(
        pagamento_status="confirmado",
        pagamento_confirmado_em=inicio + timedelta(days=1),
    )
    lembrete_cobranca = SimpleNamespace(
        idempotency_key=_chave_lembrete_fluxo(proposta, "cobrar_pagamento"),
        status="pendente",
        concluido_em=None,
        concluido_por=None,
    )
    session = FakeSession(
        [
            FakeResult(itens=[(proposta, _lead())]),
            FakeResult(itens=[]),
            FakeResult(itens=[lembrete_cobranca]),
            FakeResult(scalar=102),
        ]
    )

    resultado = await reconciliar_automacoes_fluxo_contratacao(session)

    assert resultado == {"criados": 1, "concluidos": 1, "propostas_avaliadas": 1}
    assert lembrete_cobranca.status == "concluido"
    assert lembrete_cobranca.concluido_em is not None
    assert lembrete_cobranca.concluido_por == "Automação (fluxo da contratação)"


@pytest.mark.asyncio
async def test_reconciliacao_vazia_e_idempotente() -> None:
    session = FakeSession([FakeResult(itens=[]), FakeResult(itens=[]), FakeResult(itens=[])])
    assert await reconciliar_automacoes_fluxo_contratacao(session) == {
        "criados": 0,
        "concluidos": 0,
        "propostas_avaliadas": 0,
    }


@pytest.mark.asyncio
async def test_estorno_nao_conclui_tarefa_juridica_como_sucesso() -> None:
    inicio = datetime(2026, 9, 1, tzinfo=UTC)
    proposta = _proposta(
        pagamento_status="pendente",
        pagamento_confirmado_em=None,
        juridico_recebido_em=inicio + timedelta(days=2),
        sla_inicio_em=inicio + timedelta(days=2),
        sla_prazo_em=inicio + timedelta(days=3),
    )
    lembrete_protocolo = SimpleNamespace(
        idempotency_key=_chave_lembrete_fluxo(proposta, "protocolar_no_prazo"),
        status="pendente",
        concluido_em=None,
        concluido_por=None,
    )
    session = FakeSession(
        [
            FakeResult(itens=[(proposta, _lead())]),
            FakeResult(itens=[]),
            FakeResult(itens=[lembrete_protocolo]),
            FakeResult(scalar=103),
        ]
    )

    resultado = await reconciliar_automacoes_fluxo_contratacao(session)

    assert resultado["criados"] == 1  # a cobrança volta a ser necessária
    assert resultado["concluidos"] == 0
    assert lembrete_protocolo.status == "pendente"

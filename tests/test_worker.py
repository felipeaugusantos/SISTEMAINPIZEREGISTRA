"""Testes do dispatcher de jobs de manutenção (app/worker.py::processar).

Fase 3 da missão de maturidade técnica (14/09/2026): o dispatcher tem ~28
tipos de job num único if/elif, e boa parte não tinha nenhum teste -- os
dois bugs reais encontrados ao vivo em produção nesta mesma sessão (fallback
do Gemini e duplicidade de idempotency_key) nasceram exatamente nesse tipo
de código automático sem teste cobrindo o caminho de erro. Começa por dois
dos handlers de maior risco (dinheiro/prazo e disparo de e-mail em massa);
os demais handlers sem teste ficam para rodadas seguintes.

Isolados e determinísticos: usam FakeSession (tests/conftest.py) e
monkeypatch em app.worker.session_factory -- nunca tocam o banco de verdade.
"""

import asyncio
from datetime import date

import pytest

import app.worker as worker_modulo
from app.models import AlertaSistema, Movimentacao
from tests.conftest import FakeResult, FakeSession


class _ContextoSessaoFalso:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _movimentacao(**kwargs: object) -> Movimentacao:
    base: dict = {
        "id": 1,
        "processo_id": 10,
        "codigo_despacho": "IPC262",
        "descricao": "Concessão de Registro",
        "data_rpi": date(2016, 9, 6),
        "numero_rpi": 2380,
        "fonte_arquivo": "rpi-2380.zip",
        "chave_origem": "rpi-2380:10:IPC262",
    }
    base.update(kwargs)
    return Movimentacao(**base)


# --- crm.gerar_renovacoes_marca ---------------------------------------------


def test_gerar_renovacoes_marca_cria_renovacao_a_partir_da_concessao(monkeypatch: pytest.MonkeyPatch) -> None:
    movimentacao = _movimentacao()
    session = FakeSession(
        [
            FakeResult(itens=[(1, 10)]),  # pendentes: (organizacao_id, processo_id)
            FakeResult(itens=[movimentacao]),  # movimentacoes do processo 10
            FakeResult(scalar=55),  # insert retornou id novo (nao houve conflito)
        ]
    )
    monkeypatch.setattr(worker_modulo, "session_factory", lambda: _ContextoSessaoFalso(session))

    asyncio.run(worker_modulo.processar("crm.gerar_renovacoes_marca", {}))

    assert session.commits == 1
    alertas = [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    assert len(alertas) == 1
    assert alertas[0].codigo == "RENOVACOES_GERADAS"
    assert alertas[0].detalhes == {"criadas": 1}


def test_gerar_renovacoes_marca_calcula_vencimento_dez_anos_apos_concessao(monkeypatch: pytest.MonkeyPatch) -> None:
    """A referência de vencimento é a data da movimentação de concessão, não
    a data de execução do job -- confirma que _somar_anos(data_concessao, 10)
    é o valor efetivamente usado na criação (via inspeção do INSERT emitido)."""
    movimentacao = _movimentacao(data_rpi=date(2016, 9, 6))
    session = FakeSession(
        [
            FakeResult(itens=[(1, 10)]),
            FakeResult(itens=[movimentacao]),
            FakeResult(scalar=55),
        ]
    )
    monkeypatch.setattr(worker_modulo, "session_factory", lambda: _ContextoSessaoFalso(session))

    asyncio.run(worker_modulo.processar("crm.gerar_renovacoes_marca", {}))

    insert_stmt = session.executados[2]
    parametros = insert_stmt.compile().params
    assert parametros["vencimento"] == date(2026, 9, 6)
    assert parametros["referencia"] == "registro-2016-09-06"


def test_gerar_renovacoes_marca_pula_quando_sem_movimentacao_de_concessao(monkeypatch: pytest.MonkeyPatch) -> None:
    """Processo aparece como "registrada" mas nenhuma movimentação bate com
    o despacho de concessão (dado incompleto/importação parcial) -- não deve
    quebrar nem criar renovação com vencimento incorreto, só pular."""
    movimentacao_sem_concessao = _movimentacao(codigo_despacho="IPC271", descricao="Publicação de pedido")
    session = FakeSession(
        [
            FakeResult(itens=[(1, 10)]),
            FakeResult(itens=[movimentacao_sem_concessao]),
        ]
    )
    monkeypatch.setattr(worker_modulo, "session_factory", lambda: _ContextoSessaoFalso(session))

    asyncio.run(worker_modulo.processar("crm.gerar_renovacoes_marca", {}))

    assert session.commits == 1
    assert not [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    # Só 2 execuções (pendentes + movimentacoes) -- nenhum INSERT tentado.
    assert len(session.executados) == 2


def test_gerar_renovacoes_marca_nao_gera_alerta_quando_insert_conflita(monkeypatch: pytest.MonkeyPatch) -> None:
    """on_conflict_do_nothing protege contra corrida (duas execuções do job
    processando o mesmo processo ao mesmo tempo) -- insert devolve None, e o
    job não deve contar como criada nem gerar AlertaSistema."""
    movimentacao = _movimentacao()
    session = FakeSession(
        [
            FakeResult(itens=[(1, 10)]),
            FakeResult(itens=[movimentacao]),
            FakeResult(scalar=None),  # on_conflict_do_nothing: já existia
        ]
    )
    monkeypatch.setattr(worker_modulo, "session_factory", lambda: _ContextoSessaoFalso(session))

    asyncio.run(worker_modulo.processar("crm.gerar_renovacoes_marca", {}))

    assert not [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)]


# --- cadencia.enviar_emails_pendentes ---------------------------------------


def test_cadencia_enviar_emails_pendentes_cria_alerta_quando_ha_envios(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession([])
    monkeypatch.setattr(worker_modulo, "session_factory", lambda: _ContextoSessaoFalso(session))

    async def _resultado_fake(_session):
        return {"enviados": 3, "falhas": 0}

    monkeypatch.setattr(worker_modulo, "processar_envios_cadencia_pendentes", _resultado_fake)

    asyncio.run(worker_modulo.processar("cadencia.enviar_emails_pendentes", {}))

    alertas = [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    assert len(alertas) == 1
    assert alertas[0].codigo == "CADENCIA_EMAILS_PROCESSADOS"
    assert alertas[0].severidade == "info"
    assert "3 e-mail(s)" in alertas[0].mensagem


def test_cadencia_enviar_emails_pendentes_alerta_severo_quando_ha_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession([])
    monkeypatch.setattr(worker_modulo, "session_factory", lambda: _ContextoSessaoFalso(session))

    async def _resultado_fake(_session):
        return {"enviados": 1, "falhas": 2}

    monkeypatch.setattr(worker_modulo, "processar_envios_cadencia_pendentes", _resultado_fake)

    asyncio.run(worker_modulo.processar("cadencia.enviar_emails_pendentes", {}))

    alertas = [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    assert len(alertas) == 1
    assert alertas[0].severidade == "aviso"


def test_cadencia_enviar_emails_pendentes_sem_atividade_nao_gera_alerta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Achado potencial de ruído: sem isso, o job rodando de hora em hora
    sem nada pra enviar geraria um AlertaSistema vazio a cada execução."""
    session = FakeSession([])
    monkeypatch.setattr(worker_modulo, "session_factory", lambda: _ContextoSessaoFalso(session))

    async def _resultado_fake(_session):
        return {"enviados": 0, "falhas": 0}

    monkeypatch.setattr(worker_modulo, "processar_envios_cadencia_pendentes", _resultado_fake)

    asyncio.run(worker_modulo.processar("cadencia.enviar_emails_pendentes", {}))

    assert not [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)]


# --- Fase 3 da Operação Jurídica: dispatcher da comunicação durável ---------


def test_worker_processa_caixa_de_saida_juridica(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession([])
    chamadas: list[FakeSession] = []
    monkeypatch.setattr(worker_modulo, "session_factory", lambda: _ContextoSessaoFalso(session))

    async def _processar(sessao):
        chamadas.append(sessao)
        return {"processados": 1, "enviados": 1, "reagendados": 0, "falhas": 0}

    monkeypatch.setattr(worker_modulo, "processar_saidas_email_juridico", _processar)
    asyncio.run(worker_modulo.processar("juridico.processar_comunicacoes", {}))

    assert chamadas == [session]
    assert session.commits == 1


def test_worker_agenda_resumo_juridico_diario(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession([])
    chamadas: list[FakeSession] = []
    monkeypatch.setattr(worker_modulo, "session_factory", lambda: _ContextoSessaoFalso(session))

    async def _agendar(sessao):
        chamadas.append(sessao)
        return 2

    monkeypatch.setattr(worker_modulo, "agendar_resumos_juridicos_diarios", _agendar)
    asyncio.run(worker_modulo.processar("juridico.agendar_resumos", {}))

    assert chamadas == [session]
    assert session.commits == 1

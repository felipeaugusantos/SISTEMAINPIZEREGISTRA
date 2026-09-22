"""Achado do usuário (22/09/2026): o histórico de atendimento do CRM

(GET /v1/admin/crm/historico) mostrava duas entradas quase idênticas para a
mesma visita de um operador -- uma genérica ("Atendimento atualizado", canal
"outro") criada automaticamente sempre que o formulário principal
("Salvar atendimento") era salvo, mesmo sem nenhuma mudança real, e outra
específica criada pelo "Novo registro" (com canal e resultado escolhidos
pelo operador). O formulário principal envia registrar_contato=true em
TODO submit, então bastava o operador salvar qualquer campo (só uma
anotação, por exemplo) para gerar um "Atendimento atualizado" vazio.

Corrigido em atualizar_status_lead (app/api/leads.py): a entrada
automática só é criada quando status, responsável ou próxima ação
realmente mudam de valor (comparado com o estado do lead antes da
mutação), não mais em todo PATCH.

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nenhum teste existente.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Lead, StatusLead
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste


def _lead_aberto(**overrides: object) -> Lead:
    base = dict(
        id=9,
        organizacao_id=1,
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        origem="processo",
        status=StatusLead.QUALIFICADO,
        fase="qualificado",
        responsavel_id=3,
        proxima_acao_em=None,
        aceite_marketing=False,
    )
    base.update(overrides)
    lead = Lead(**base)
    lead.criado_em = lead.atualizado_em = datetime.now(UTC)
    return lead


def _patch(payload: dict, *, resultados: list[FakeResult], lead_inicial: Lead | None = None) -> tuple[object, FakeSession]:
    lead = lead_inicial if lead_inicial is not None else _lead_aberto()
    session = FakeSession([FakeResult(scalar=lead), *resultados])

    async def _sessao():
        yield session

    app.dependency_overrides[get_session] = _sessao
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    resposta = TestClient(app).patch(
        "/v1/admin/leads/9",
        json={**payload, "registrar_contato": True},
        headers={"X-CSRF-Token": "csrf-teste"},
    )
    return resposta, session


def test_salvar_atendimento_sem_mudanca_nao_cria_contato_automatico() -> None:
    """Só notas: nada de status/responsável/próxima ação muda -- não deve
    poluir o histórico com um "Atendimento atualizado" vazio."""
    lead_refetch = _lead_aberto()
    try:
        resposta, session = _patch(
            {"notas": "Só uma anotação interna"},
            resultados=[FakeResult(scalar=lead_refetch)],
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    assert not any(type(x).__name__ == "ContatoLead" for x in session.adicionados)


def test_descartar_lead_com_motivo_cria_contato_por_mudanca_de_status() -> None:
    """Mudança real de status ainda deve continuar registrando o
    atendimento automático (mesmo padrão de
    test_audit_hipotese4_descarte_sem_motivo.py: aberta=False depois do
    descarte, então só sobra a query de cadências automáticas + o refetch
    final)."""
    lead_refetch = _lead_aberto(status=StatusLead.DESCARTADO)
    try:
        resposta, session = _patch(
            {"status": "descartado", "motivo_perda": "sem_resposta"},
            resultados=[
                FakeResult(itens=[]),  # aplicar_cadencias_automaticas
                FakeResult(scalar=lead_refetch),  # refetch final
            ],
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    contatos = [x for x in session.adicionados if type(x).__name__ == "ContatoLead"]
    assert len(contatos) == 1
    assert contatos[0].resultado == "Atendimento atualizado"


def test_reenviar_proxima_acao_truncada_para_minuto_nao_cria_contato() -> None:
    """Achado do Codex (PR #112): o <input type="datetime-local"> do
    formulário só tem precisão de minuto -- reenviar o mesmo horário (sem
    editar) perde os segundos/microssegundos que datetime.now(UTC) +
    timedelta normalmente grava (ex.: _garantir_proxima_acao_padrao), e uma
    comparação por igualdade exata faria parecer que mudou."""
    proxima_com_segundos = datetime(2026, 10, 1, 9, 30, 45, 123456, tzinfo=UTC)
    lead_inicial = _lead_aberto(proxima_acao_em=proxima_com_segundos)
    lead_refetch = _lead_aberto(proxima_acao_em=proxima_com_segundos)
    try:
        resposta, session = _patch(
            {"proxima_acao_em": "2026-10-01T09:30:00+00:00"},
            resultados=[
                FakeResult(scalar=None),  # lembrete manual existente (nenhum)
                FakeResult(scalar=None),  # obter_politica_crm (usa defaults)
                FakeResult(scalar=lead_refetch),  # refetch final
            ],
            lead_inicial=lead_inicial,
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    assert not any(type(x).__name__ == "ContatoLead" for x in session.adicionados)

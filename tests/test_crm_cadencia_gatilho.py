"""Achado 17.3 da auditoria fina do CRM: Cadencia.gatilho_evento/gatilho_valor
existiam no modelo e app.crm.aplicar_cadencias_automaticas já os consumia
(mudança de status no PATCH do lead e de fase em avancar_fase_lead), mas
nenhuma API nem tela permitia preenchê-los -- o disparo automático de
cadência era inalcançável. Agora a criação/edição de cadência aceita o
gatilho, validado contra o mesmo vocabulário dos chamadores."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.crm_admin import CadenciaInput
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.crm import avancar_fase_lead
from app.models import Cadencia, Lead, StatusLead
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste


def test_cadencia_sem_gatilho_continua_manual() -> None:
    dados = CadenciaInput(nome="Follow-up padrão")
    assert dados.gatilho_evento is None
    assert dados.gatilho_valor is None


def test_cadencia_aceita_gatilho_de_status_e_de_fase() -> None:
    por_status = CadenciaInput(nome="Reengajar", gatilho_evento="status", gatilho_valor="sem_retorno")
    por_fase = CadenciaInput(nome="Pós-proposta", gatilho_evento="fase", gatilho_valor="proposta_enviada")
    assert (por_status.gatilho_evento, por_status.gatilho_valor) == ("status", "sem_retorno")
    assert (por_fase.gatilho_evento, por_fase.gatilho_valor) == ("fase", "proposta_enviada")


@pytest.mark.parametrize(
    "evento,valor",
    [
        ("status", None),  # evento sem valor
        (None, "sem_retorno"),  # valor sem evento
        ("status", "proposta_aceita"),  # é fase, não status -- nunca dispararia
        ("fase", "sem_retorno"),  # é status, não fase -- nunca dispararia
        ("fase", "inexistente"),
        # Estados iniciais: o lead nasce neles e a criação não dispara nada.
        ("status", "novo"),
        ("fase", "contato_inicial"),
    ],
)
def test_cadencia_recusa_gatilho_que_nunca_dispararia(evento: str | None, valor: str | None) -> None:
    with pytest.raises(ValidationError):
        CadenciaInput(nome="Inválida", gatilho_evento=evento, gatilho_valor=valor)


def test_cadencia_recusa_evento_desconhecido() -> None:
    with pytest.raises(ValidationError):
        CadenciaInput(nome="Inválida", gatilho_evento="pendencia", gatilho_valor="sla_protocolo")


def _cliente_com_sessao(session: FakeSession) -> TestClient:
    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    usuario = usuario_teste("operador", {"crm.view", "crm.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return TestClient(app)


def test_criar_cadencia_grava_gatilho_automatico() -> None:
    session = FakeSession([])
    try:
        resposta = _cliente_com_sessao(session).post(
            "/v1/admin/crm/cadencias",
            json={
                "nome": "Reengajar sem retorno",
                "gatilho_evento": "status",
                "gatilho_valor": "sem_retorno",
                "passos": [{"dia": 0, "canal": "email", "titulo": "Primeiro e-mail"}],
            },
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 201
    cadencia = next(obj for obj in session.adicionados if isinstance(obj, Cadencia))
    assert cadencia.gatilho_evento == "status"
    assert cadencia.gatilho_valor == "sem_retorno"


def test_editar_cadencia_remove_gatilho_quando_vira_manual() -> None:
    existente = Cadencia(
        id=5,
        organizacao_id=1,
        nome="Antiga",
        ativo=True,
        gatilho_evento="fase",
        gatilho_valor="proposta_enviada",
        passos=[],
    )
    session = FakeSession([FakeResult(scalar=existente)])
    try:
        resposta = _cliente_com_sessao(session).put(
            "/v1/admin/crm/cadencias/5",
            json={"nome": "Antiga", "gatilho_evento": None, "gatilho_valor": None, "passos": []},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200
    assert existente.gatilho_evento is None
    assert existente.gatilho_valor is None


def test_criar_cadencia_com_gatilho_invalido_devolve_422() -> None:
    session = FakeSession([])
    try:
        resposta = _cliente_com_sessao(session).post(
            "/v1/admin/crm/cadencias",
            json={"nome": "Inválida", "gatilho_evento": "status", "gatilho_valor": "ganho", "passos": []},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 422
    assert not [obj for obj in session.adicionados if isinstance(obj, Cadencia)]


# --- Revisão do Codex no PR #145: o evento "status" só era disparado pelo
# PATCH de status. O Kanban e avancar_fase_lead (proposta enviada, conversão)
# mudavam o status em silêncio, e uma cadência de "Sem retorno" ou
# "Convertido" nunca rodava por esses caminhos. ---


def _registrar_disparos(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    import app.crm as crm_modulo

    disparos: list[tuple[str, str]] = []

    async def _regras(_session, _lead, evento, valor, _por):
        disparos.append((evento, valor))
        return []

    async def _cadencias(_session, _lead, _evento, _valor, _por):
        return 0

    monkeypatch.setattr(crm_modulo, "aplicar_regras_automacao", _regras)
    monkeypatch.setattr(crm_modulo, "aplicar_cadencias_automaticas", _cadencias)
    return disparos


def _lead(**kwargs: object) -> Lead:
    base: dict = {
        "id": 9,
        "organizacao_id": 1,
        "nome": "Fulano",
        "email": "fulano@example.com",
        "telefone": "11999998888",
        "marca": "ACME",
        "origem": "processo",
        "status": StatusLead.NOVO,
        "fase": "contato_inicial",
        "responsavel_id": 3,
        "proxima_acao_em": datetime.now(UTC),
        "aceite_marketing": False,
    }
    base.update(kwargs)
    return Lead(**base)


def test_avancar_fase_dispara_evento_status_quando_o_status_muda(monkeypatch: pytest.MonkeyPatch) -> None:
    disparos = _registrar_disparos(monkeypatch)
    lead = _lead(status=StatusLead.QUALIFICADO, fase="qualificado")

    asyncio.run(avancar_fase_lead(FakeSession([]), lead, "proposta_enviada", "Operador"))

    assert ("fase", "proposta_enviada") in disparos
    assert ("status", "proposta_enviada") in disparos


def test_avancar_fase_sem_mudanca_de_status_nao_dispara_evento_status(monkeypatch: pytest.MonkeyPatch) -> None:
    disparos = _registrar_disparos(monkeypatch)
    lead = _lead(status=StatusLead.PROPOSTA_ENVIADA, fase="proposta_enviada")

    asyncio.run(avancar_fase_lead(FakeSession([]), lead, "proposta_aceita", "Operador"))

    assert disparos == [("fase", "proposta_aceita")]


def test_mover_kanban_para_aguardando_retorno_dispara_evento_sem_retorno(monkeypatch: pytest.MonkeyPatch) -> None:
    disparos = _registrar_disparos(monkeypatch)
    lead = _lead(status=StatusLead.EM_CONTATO)
    session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=None)])
    try:
        resposta = _cliente_com_sessao(session).post(
            "/v1/admin/leads/9/kanban",
            json={"etapa": "aguardando_retorno_cliente"},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200
    assert lead.status == StatusLead.SEM_RETORNO
    assert ("status", "sem_retorno") in disparos


def test_tela_de_cadencias_expoe_o_disparo_automatico() -> None:
    raiz = Path(__file__).resolve().parents[1] / "app" / "web"
    html = (raiz / "admin-regras-automaticas.html").read_text(encoding="utf-8")
    script = (raiz / "static" / "admin-cadencias.js").read_text(encoding="utf-8")
    assert 'id="cadencia-gatilho"' in html
    assert "gatilho_evento" in script
    assert "gatilho_valor" in script

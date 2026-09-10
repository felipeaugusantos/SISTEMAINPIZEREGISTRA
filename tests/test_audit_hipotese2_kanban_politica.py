"""Testes de regressão da auditoria técnica (10/09/2026) — Hipótese 2:
o Kanban (POST /v1/admin/leads/{lead_id}/kanban) permitiria avançar uma
oportunidade sem responsável e sem próxima ação para além do primeiro
contato?

Já existiam dois testes cobrindo só a etapa "aguardando_contato_nosso"
(test_mover_kanban_bloqueia_oportunidade_aberta_sem_proxima_acao e
test_mover_kanban_permite_oportunidade_aberta_com_proxima_acao, em
tests/test_leads.py). Este arquivo estende a mesma verificação para as
outras três etapas citadas na hipótese: aguardando_retorno_cliente,
proposta_enviada e proposta_aceita.

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Lead, StatusLead
from tests.conftest import FakeResult, auth_override, sessao_override, usuario_teste


def _lead_sem_responsavel_e_sem_proxima_acao(**overrides: object) -> Lead:
    base = dict(
        id=9,
        organizacao_id=1,
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        origem="processo",
        status=StatusLead.NOVO,
        fase="contato_inicial",
        responsavel_id=None,
        proxima_acao_em=None,
        aceite_marketing=False,
    )
    base.update(overrides)
    return Lead(**base)


def _mover(etapa: str):
    lead = _lead_sem_responsavel_e_sem_proxima_acao()
    # 1ª query: fetch do lead. 2ª query: obter_politica_crm (nenhuma
    # cadastrada -> default em memória, exigir_responsavel=True e
    # exigir_proxima_acao=True).
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return TestClient(app).post(
        "/v1/admin/leads/9/kanban",
        json={"etapa": etapa},
        headers={"X-CSRF-Token": "csrf-teste"},
    )


def test_kanban_bloqueia_mover_para_aguardando_retorno_cliente_sem_responsavel() -> None:
    """CONFIRMADO: a política NÃO é contornada. mover_lead_kanban chama
    aplicar_politica_oportunidade(session, lead, usuario.id) sempre que o
    status resultante não é CONVERTIDO/DESCARTADO -- "aguardando_retorno_cliente"
    define status=SEM_RETORNO, que passa por essa checagem."""
    resposta = _mover("aguardando_retorno_cliente")
    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert "responsável" in detalhe
    assert "próxima ação" in detalhe


def test_kanban_bloqueia_mover_para_proposta_enviada_sem_responsavel() -> None:
    """CONFIRMADO: fase "proposta_enviada" mapeia para
    StatusLead.PROPOSTA_ENVIADA (MAPA_FASE_STATUS em app/crm.py) -- não é
    CONVERTIDO/DESCARTADO, então a política também é checada aqui."""
    resposta = _mover("proposta_enviada")
    assert resposta.status_code == 422
    assert "responsável" in resposta.json()["detail"]


def test_kanban_bloqueia_mover_para_proposta_aceita_sem_responsavel() -> None:
    """CONFIRMADO, com uma nuance: "proposta_aceita" NÃO está em
    MAPA_FASE_STATUS (app/crm.py) -- ao contrário das outras etapas
    testadas aqui, mover_lead_kanban não muda lead.status ao entrar nessa
    fase (ele permanece o que já era, aqui NOVO). Como NOVO também não é
    CONVERTIDO/DESCARTADO, a política ainda é aplicada e o bloqueio ocorre
    -- mas por uma razão de código ligeiramente diferente das demais
    etapas (nenhum status novo é atribuído, o antigo já bastava para não
    cair no bypass)."""
    resposta = _mover("proposta_aceita")
    assert resposta.status_code == 422
    assert "responsável" in resposta.json()["detail"]


def test_kanban_permite_mover_para_proposta_aceita_com_responsavel_e_proxima_acao() -> None:
    """Contraponto: com os dois campos preenchidos, a mesma etapa é aceita
    (confirma que o bloqueio acima é da política, não de outra validação)."""
    lead = _lead_sem_responsavel_e_sem_proxima_acao(responsavel_id=3, proxima_acao_em=datetime.now(UTC))
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/9/kanban",
        json={"etapa": "proposta_aceita"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200

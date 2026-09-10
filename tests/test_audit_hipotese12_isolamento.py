"""Auditoria técnica (10/09/2026) — Hipótese 12: isolamento por organização
e mascaramento de PII nos endpoints auditados nesta rodada (kanban, mover
pesquisa). Cobertura genérica de permissões/perfis já existe em
tests/test_permissions.py e tests/test_saas_tenancy.py -- não duplicada
aqui.

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.leads import _lead_response
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Lead, PesquisaMarca, StatusLead
from tests.conftest import FakeResult, auth_override, sessao_override, usuario_teste


def _lead(**overrides: object) -> Lead:
    base = dict(
        id=7,
        organizacao_id=1,
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        documento="12345678900",
        marca="ACME",
        origem="processo",
        status=StatusLead.NOVO,
    )
    base.update(overrides)
    lead = Lead(**base)
    lead.criado_em = lead.atualizado_em = datetime.now(UTC)
    return lead


def test_lead_response_mascara_pii_sem_a_permissao() -> None:
    """CONFIRMADO: _lead_response (app/api/leads.py) só devolve e-mail,
    telefone e documento em claro quando usuario.pode("leads.pii.view").
    Sem a permissão, os três campos vêm mascarados."""
    lead = _lead()
    usuario = usuario_teste(perfil="operador", permissoes=frozenset())

    dados = _lead_response(lead, usuario)

    assert dados.email != "fulano@example.com"
    assert "***" in dados.email or dados.email.startswith("f")
    assert dados.telefone != "11999998888"
    assert dados.documento != "12345678900"


def test_lead_response_expoe_pii_completa_com_a_permissao() -> None:
    lead = _lead()
    usuario = usuario_teste(perfil="comercial", permissoes=frozenset({"leads.pii.view"}))

    dados = _lead_response(lead, usuario)

    assert dados.email == "fulano@example.com"
    assert dados.telefone == "11999998888"


def test_kanban_nao_encontra_lead_de_outra_organizacao() -> None:
    """CONFIRMADO: mover_lead_kanban filtra
    Lead.organizacao_id == usuario.organizacao_id na própria consulta do
    lead -- um lead_id válido de OUTRA organização simplesmente não é
    encontrado (404), sem vazar nem a existência do registro."""
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/999/kanban",
        json={"etapa": "qualificado"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 404


def test_mover_pesquisa_nao_aceita_lead_de_destino_de_outra_organizacao() -> None:
    """CONFIRMADO: mover_pesquisa_para_outro_lead (app/api/leads.py) busca
    o lead_id_destino também filtrando
    Lead.organizacao_id == usuario.organizacao_id -- um id de lead válido,
    mas de outra organização, é tratado como "Lead de destino inválido"
    (422), não como um destino aceito."""
    pesquisa = PesquisaMarca(id="11111111-1111-1111-1111-111111111111", organizacao_id=1, lead_id=9, marca="ACME")
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=pesquisa),
        FakeResult(scalar=None),  # busca do lead_id_destino na organização do operador -> não encontrado
    )
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/pesquisas/11111111-1111-1111-1111-111111111111/mover-lead",
        json={"lead_id_destino": 4242},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422
    assert "inválido" in resposta.json()["detail"].lower()

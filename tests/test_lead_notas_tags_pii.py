"""Achado P1 do Codex no PR #134 (Fase 15.3 da auditoria fina de Leads,
23/09/2026): _lead_response passou a ocultar "notas"/"tags" pra quem não
tem leads.pii.view (mesmo tratamento já dado a e-mail/telefone/
documento), mas admin-leads.js continuava mandando os dois campos em
todo PATCH de "Salvar atendimento". Pra esse usuário o formulário nunca
viu o valor real -- então o payload chegava com notas=None/tags=[] e
apagava de vez o que já existia, só por mudar o status ou o responsável.

Corrigido em atualizar_status_lead (app/api/leads.py): sem
leads.pii.view, as chaves "notas"/"tags" do payload são ignoradas,
preservando o que já estava salvo. E em admin-leads.js: os campos nem
aparecem no formulário nem são incluídos no payload pra esse usuário.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Lead, StatusLead
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste


def _lead_com_notas_e_tags(**overrides: object) -> Lead:
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
        proxima_acao_em=datetime(2099, 1, 1, tzinfo=UTC),
        aceite_marketing=False,
        notas="CPF do titular: 123.456.789-00",
        tags=["vip"],
    )
    base.update(overrides)
    lead = Lead(**base)
    lead.criado_em = lead.atualizado_em = datetime.now(UTC)
    return lead


def _patch(payload: dict, usuario, *, resultados: list[FakeResult], lead_inicial: Lead) -> tuple[object, Lead]:
    session = FakeSession([FakeResult(scalar=lead_inicial), *resultados])

    async def _sessao():
        yield session

    app.dependency_overrides[get_session] = _sessao
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).patch(
            "/v1/admin/leads/9",
            json={**payload, "registrar_contato": False},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    return resposta, lead_inicial


def test_patch_sem_permissao_pii_nao_apaga_notas_nem_tags() -> None:
    # Mesmo payload que admin-leads.js mandava antes da correção: o
    # formulário nunca mostrou o valor real, então notas/tags chegam
    # "vazios" -- o backend não pode confiar nisso.
    usuario = usuario_teste("operador", {"leads.view", "leads.manage"})
    lead = _lead_com_notas_e_tags()

    resposta, _ = _patch(
        {"status": "qualificado", "notas": None, "tags": []},
        usuario,
        # 2 consultas depois da busca inicial: obter_politica_crm (dentro
        # de aplicar_politica_oportunidade, disparada por mudar o status) e
        # o refetch final que monta a resposta.
        resultados=[FakeResult(scalar=None), FakeResult(scalar=lead)],
        lead_inicial=lead,
    )

    assert resposta.status_code == 200
    assert lead.notas == "CPF do titular: 123.456.789-00"
    assert lead.tags == ["vip"]


def test_patch_com_permissao_pii_atualiza_notas_e_tags_normalmente() -> None:
    usuario = usuario_teste()  # administrador: tem leads.pii.view
    lead = _lead_com_notas_e_tags()

    resposta, _ = _patch(
        {"status": "qualificado", "notas": "Nova anotação", "tags": ["urgente"]},
        usuario,
        resultados=[FakeResult(scalar=None), FakeResult(scalar=lead)],
        lead_inicial=lead,
    )

    assert resposta.status_code == 200
    assert lead.notas == "Nova anotação"
    assert lead.tags == ["urgente"]

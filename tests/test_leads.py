from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.leads import (
    _lead_response,
    _resumo_pesquisa,
    _valor_csv,
    limitar_acoes_admin,
    limitar_admin,
    limitar_leads,
)
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import EventoAuditoria, Lead, PesquisaMarca, StatusLead, VersaoRelatorioMarca
from app.settings import get_settings
from app.trademarks.analysis_workflow import EstadoAnalise
from tests.conftest import FakeResult, FakeSession, auth_override, sessao_override, usuario_teste
from tests.test_relatorio_pdf import _relatorio_exemplo

_settings = get_settings()
CREDENCIAIS_OK = (_settings.admin_username, _settings.admin_password)


@pytest.fixture(autouse=True)
def _reset_estado() -> None:
    limitar_leads.limpar()
    limitar_admin.limpar()
    limitar_acoes_admin.limpar()
    yield
    app.dependency_overrides.clear()
    limitar_leads.limpar()
    limitar_admin.limpar()
    limitar_acoes_admin.limpar()


def _payload(**overrides: object) -> dict[str, object]:
    base = {
        "nome": "Fulano de Tal",
        "email": "fulano@example.com",
        "telefone": "11999998888",
        "marca": "ACME",
        "processo_numero": "123456789",
        "origem": "processo",
        "aceite_privacidade": True,
        "website": "",
    }
    base.update(overrides)
    return base


def test_honeypot_rejeita_envio() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    resposta = TestClient(app).post("/v1/leads", json=_payload(website="http://bot"))
    assert resposta.status_code == 400


def test_email_invalido_retorna_422() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    resposta = TestClient(app).post("/v1/leads", json=_payload(email="sem-arroba"))
    assert resposta.status_code == 422


def test_consentimento_obrigatorio() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    resposta = TestClient(app).post("/v1/leads", json=_payload(aceite_privacidade=False))
    assert resposta.status_code == 422


def test_lead_criado_sem_marca() -> None:
    # A busca é desacoplada da coleta de PII: 'marca' passou a ser opcional.
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    payload = _payload()
    del payload["marca"]
    resposta = TestClient(app).post("/v1/leads", json=payload)
    assert resposta.status_code == 201
    assert resposta.json()["id"] == 1


def test_rate_limit_bloqueia_excesso() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    cliente = TestClient(app)
    # O honeypot devolve 400 depois do limitador; as 10 primeiras contam no limite.
    for _ in range(10):
        assert cliente.post("/v1/leads", json=_payload(website="http://bot")).status_code == 400
    assert cliente.post("/v1/leads", json=_payload(website="http://bot")).status_code == 429


def test_admin_sem_credenciais() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    assert TestClient(app).get("/v1/admin/leads").status_code == 401


def test_admin_credenciais_invalidas() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    resposta = TestClient(app).get("/v1/admin/leads", auth=("admin", "errada"))
    assert resposta.status_code == 401


def test_admin_lista_com_credenciais() -> None:
    lead = Lead(
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        processo_numero="123456789",
        origem="processo",
        tipo_interesse=None,
        status=StatusLead.NOVO,
    )
    lead.id = 1
    lead.criado_em = datetime.now(UTC)
    lead.atualizado_em = datetime.now(UTC)

    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=1),
        FakeResult(scalar=1),
        FakeResult(itens=[lead]),
        FakeResult(itens=[(StatusLead.NOVO, 1)]),
        FakeResult(scalar=0),
        FakeResult(itens=[]),
    )
    app.dependency_overrides[obter_usuario_atual] = auth_override()
    resposta = TestClient(app).get("/v1/admin/leads")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["itens"][0]["email"] == "fulano@example.com"
    assert corpo["itens"][0]["processo_numero"] == "123456789"
    assert corpo["itens"][0]["origem"] == "processo"
    assert corpo["por_status"]["novo"] == 1


def test_resumo_crm_apresenta_prioridades_comerciais() -> None:
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(itens=[(2, 1, 3)]),
    )
    app.dependency_overrides[obter_usuario_atual] = auth_override()

    resposta = TestClient(app).get("/v1/admin/leads-crm")

    assert resposta.status_code == 200
    assert resposta.json()["sem_responsavel"] == 2
    assert resposta.json()["atrasadas"] == 1
    assert resposta.json()["sem_proxima_acao"] == 3


def test_admin_abre_contato_com_historico_de_pesquisas() -> None:
    lead = Lead(
        organizacao_id=1,
        nome="Enzo",
        email="enzo@empresa.com.br",
        telefone="16999998888",
        marca="MARCA INICIAL",
        origem="relatorio",
        status=StatusLead.NOVO,
    )
    lead.id = 22
    lead.criado_em = datetime(2026, 8, 1, tzinfo=UTC)
    lead.atualizado_em = lead.criado_em
    pesquisa = PesquisaMarca(
        id="pesquisa-contato",
        organizacao_id=1,
        lead_id=lead.id,
        marca="MARCA MAIS RECENTE",
        atividade="Tecnologia",
        tipo_pesquisa="completa",
    )
    pesquisa.criado_em = datetime(2026, 8, 8, tzinfo=UTC)

    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=lead),
        FakeResult(itens=[(pesquisa, "alto", 72, True, None)]),
    )
    app.dependency_overrides[obter_usuario_atual] = auth_override()

    resposta = TestClient(app).get(f"/v1/admin/leads/{lead.id}")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["nome"] == "Enzo"
    assert corpo["total_pesquisas"] == 1
    assert corpo["ultima_pesquisa"]["id"] == pesquisa.id
    assert corpo["pesquisas"][0]["marca"] == "MARCA MAIS RECENTE"


def test_leituras_admin_nao_sao_bloqueadas_por_rate_limit() -> None:
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=0),
        FakeResult(scalar=0),
        FakeResult(itens=[]),
        FakeResult(itens=[]),
        FakeResult(scalar=0),
    )
    app.dependency_overrides[obter_usuario_atual] = auth_override()
    cliente = TestClient(app)

    for _ in range(25):
        resposta = cliente.get("/v1/admin/leads")
        assert resposta.status_code == 200


def test_http_basic_nao_autentica_mais() -> None:
    cliente = TestClient(app)
    resposta = cliente.get("/admin", auth=CREDENCIAIS_OK, follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/login")


def test_exportacao_csv_neutraliza_formula_de_planilha() -> None:
    assert _valor_csv('=HYPERLINK("https://malicioso")').startswith("'=")
    assert _valor_csv("@comando").startswith("'@")
    assert _valor_csv("Empresa normal") == "Empresa normal"


def test_contato_fica_mascarado_sem_permissao_pii() -> None:
    lead = Lead(
        organizacao_id=1,
        nome="Contato",
        email="contato@empresa.com.br",
        telefone="11999998888",
        marca="ACME",
        origem="relatorio",
        status=StatusLead.NOVO,
    )
    lead.id = 10
    lead.criado_em = datetime.now(UTC)
    lead.atualizado_em = datetime.now(UTC)
    resposta = _lead_response(lead, usuario_teste("operador", {"leads.view"}))
    assert resposta.email == "c***@empresa.com.br"
    assert resposta.telefone == "***8888"


def test_pesquisa_sobrevive_a_exclusao_do_contato() -> None:
    fk = next(iter(PesquisaMarca.__table__.c.lead_id.foreign_keys))
    assert PesquisaMarca.__table__.c.lead_id.nullable is True
    assert fk.ondelete == "SET NULL"


def test_resumo_distingue_relatorio_completo_gerado_pelo_time() -> None:
    pesquisa = PesquisaMarca(
        id="pesquisa-1",
        organizacao_id=1,
        marca="ACME",
        atividade="Tecnologia",
        tipo_pesquisa="completa",
        relatorio_completo_gerado_em=None,
    )
    pesquisa.criado_em = datetime.now(UTC)

    pendente = _resumo_pesquisa(pesquisa, "moderado", 40, True)
    assert pendente.relatorio_disponivel is True
    assert pendente.relatorio_completo_gerado is False
    assert pendente.relatorio_completo_gerado_em is None

    pesquisa.relatorio_completo_gerado_em = datetime.now(UTC)
    pesquisa.relatorio_completo_gerado_por = "Admin Teste"
    pesquisa.analysis_state = EstadoAnalise.VALIDATED.value
    pesquisa.validated_at = datetime.now(UTC)
    pesquisa.validated_by = "Revisor Teste"
    gerado = _resumo_pesquisa(pesquisa, "moderado", 40, True)
    assert gerado.relatorio_completo_gerado is True
    assert gerado.relatorio_completo_gerado_por == "Admin Teste"


def test_resumo_do_contato_agrega_historico_risco_e_relatorios() -> None:
    lead = Lead(
        organizacao_id=1,
        nome="Contato",
        email="contato@empresa.com.br",
        telefone="11999998888",
        marca="MARCA ANTIGA",
        origem="relatorio",
        status=StatusLead.NOVO,
    )
    lead.id = 20
    lead.criado_em = datetime(2026, 1, 1, tzinfo=UTC)
    lead.atualizado_em = lead.criado_em

    antiga = PesquisaMarca(
        id="pesquisa-antiga",
        organizacao_id=1,
        marca="MARCA ANTIGA",
        atividade="Comércio",
        tipo_pesquisa="completa",
        relatorio_completo_gerado_em=datetime(2026, 2, 2, tzinfo=UTC),
        analysis_state=EstadoAnalise.VALIDATED.value,
        validated_at=datetime(2026, 2, 2, tzinfo=UTC),
        validated_by="Revisor Teste",
    )
    antiga.criado_em = datetime(2026, 2, 1, tzinfo=UTC)
    recente = PesquisaMarca(
        id="pesquisa-recente",
        organizacao_id=1,
        marca="MARCA NOVA",
        atividade="Tecnologia",
        tipo_pesquisa="completa",
    )
    recente.criado_em = datetime(2026, 3, 1, tzinfo=UTC)
    pesquisas = [
        _resumo_pesquisa(recente, "moderado", 48, True),
        _resumo_pesquisa(antiga, "critico", 91, True),
    ]

    resposta = _lead_response(lead, usuario_teste(), pesquisas)

    assert resposta.total_pesquisas == 2
    assert resposta.ultima_pesquisa.id == "pesquisa-recente"
    assert resposta.ultima_pesquisa_em == recente.criado_em
    assert resposta.risco_mais_alto == "critico"
    assert resposta.risco_mais_alto_pontuacao == 91
    assert resposta.relatorios_completos_gerados == 1
    assert [item.id for item in resposta.pesquisas] == ["pesquisa-recente", "pesquisa-antiga"]


def test_geracao_de_relatorio_completo_exige_autenticacao() -> None:
    resposta = TestClient(app).post("/v1/admin/pesquisas/pesquisa-1/relatorio-completo.pdf")
    assert resposta.status_code == 401


def test_operador_gera_relatorio_completo_e_registra_primeira_geracao() -> None:
    pesquisa = PesquisaMarca(
        id="pesquisa-1",
        organizacao_id=1,
        marca="ACME",
        atividade="Tecnologia",
        tipo_pesquisa="completa",
        analysis_state=EstadoAnalise.VALIDATED.value,
        validated_at=datetime.now(UTC),
        validated_by="Revisor Teste",
    )
    versao = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=1,
        schema_versao="relatorio-marca-4.2",
        conteudo_hash="hash",
        payload=_relatorio_exemplo(com_prognostico=True).model_dump(mode="json"),
        validated_at=pesquisa.validated_at,
        validated_by=pesquisa.validated_by,
        validation_notes="Versão conferida pelo especialista.",
    )
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=pesquisa),
        FakeResult(scalar=versao),
    )
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/pesquisas/pesquisa-1/relatorio-completo.pdf",
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "application/pdf"
    assert resposta.content.startswith(b"%PDF")
    assert resposta.headers["x-relatorio-completo-primeira-geracao"] == "true"
    assert resposta.headers["x-analysis-state"] == "VALIDATED"
    assert pesquisa.relatorio_completo_gerado_em is not None
    assert pesquisa.relatorio_completo_gerado_por == "admin@teste.local"


def test_relatorio_preliminar_e_permitido_enquanto_revisao_esta_pendente() -> None:
    pesquisa = PesquisaMarca(
        id="pesquisa-pendente",
        organizacao_id=1,
        marca="ACME",
        tipo_pesquisa="completa",
        analysis_state=EstadoAnalise.PENDING_REVIEW.value,
    )
    versao = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=1,
        schema_versao="relatorio-marca-4.3",
        conteudo_hash="hash-pendente",
        payload=_relatorio_exemplo().model_dump(mode="json"),
    )
    sessao = FakeSession([FakeResult(scalar=pesquisa), FakeResult(scalar=versao)])

    async def override_session():
        yield sessao

    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/pesquisas/pesquisa-pendente/relatorio-completo.pdf",
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "application/pdf"
    assert resposta.content.startswith(b"%PDF")
    assert resposta.headers["x-relatorio-status"] == "preliminar"
    assert resposta.headers["x-analysis-state"] == EstadoAnalise.PENDING_REVIEW.value
    assert pesquisa.relatorio_completo_gerado_em is not None
    assert pesquisa.relatorio_completo_gerado_por == "admin@teste.local"
    assert not any(
        isinstance(item, EventoAuditoria) and item.acao == "bloquear_relatorio"
        for item in sessao.adicionados
    )

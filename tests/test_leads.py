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
from app.models import Lead, PesquisaMarca, StatusLead, VersaoRelatorioMarca
from app.settings import get_settings
from tests.conftest import FakeResult, auth_override, sessao_override, usuario_teste
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
    gerado = _resumo_pesquisa(pesquisa, "moderado", 40, True)
    assert gerado.relatorio_completo_gerado is True
    assert gerado.relatorio_completo_gerado_por == "Admin Teste"


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
    )
    versao = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=1,
        schema_versao="relatorio-marca-4.2",
        conteudo_hash="hash",
        payload=_relatorio_exemplo(com_estimativa=True).model_dump(mode="json"),
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
    assert pesquisa.relatorio_completo_gerado_em is not None
    assert pesquisa.relatorio_completo_gerado_por == "admin@teste.local"

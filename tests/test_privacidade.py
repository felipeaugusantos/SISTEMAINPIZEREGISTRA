from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.auth import hash_token
from app.crm import registrar_consentimento_operador, registrar_consentimento_titular
from app.database import get_session
from app.main import app
from app.models import Lead, SolicitacaoAnonimizacaoLead
from tests.conftest import FakeResult, sessao_override

# --- Achado L13/L14 do plano Leads/CRM (03/09/2026): aceite_privacidade era um
# bool unico sem data, versao do termo ou base legal, e nao havia autoatendimento
# de exclusao (LGPD) para o titular. ---


def _lead(**kwargs: object) -> Lead:
    base: dict = {
        "id": 9,
        "organizacao_id": 1,
        "nome": "Fulano de Tal",
        "email": "fulano@example.com",
        "telefone": "11999998888",
        "documento": "12345678900",
        "empresa": "Fulano Comércio",
        "notas": "Ligou pedindo desconto.",
        "marca": "ACME",
        "aceite_marketing": True,
    }
    base.update(kwargs)
    return Lead(**base)


def test_registrar_consentimento_titular_marca_base_legal_e_versao() -> None:
    lead = _lead()
    registrar_consentimento_titular(lead, "2.1")
    assert lead.consentimento_base_legal == "consentimento_titular"
    assert lead.consentimento_versao_termo == "2.1"
    assert lead.consentimento_em is not None
    assert lead.consentimento_registrado_por is None


def test_registrar_consentimento_operador_nao_grava_versao_de_termo() -> None:
    lead = _lead()
    registrar_consentimento_operador(lead, operador_id=42)
    assert lead.consentimento_base_legal == "interesse_legitimo_atendimento"
    assert lead.consentimento_versao_termo is None
    assert lead.consentimento_registrado_por == 42
    assert lead.consentimento_em is not None


def test_solicitar_exclusao_sem_lead_correspondente_retorna_resposta_generica() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/privacidade/solicitar-exclusao", json={"email": "ninguem@example.com"})
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert "token_teste_local" not in corpo


def test_solicitar_exclusao_com_lead_gera_token_de_teste_local() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=9), FakeResult())
    resposta = TestClient(app).post("/v1/privacidade/solicitar-exclusao", json={"email": "fulano@example.com"})
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    # e-mail desligado em teste (settings.email_enabled=False) e ambiente != production
    # -- o fallback devolve o token para permitir testar o fluxo sem SMTP real.
    assert "token_teste_local" in corpo


def test_confirmar_exclusao_token_invalido_ou_expirado() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/privacidade/confirmar-exclusao", json={"token": "token-qualquer"})
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "erro"


def test_confirmar_exclusao_anonimiza_todos_os_leads_do_email() -> None:
    solicitacao = SolicitacaoAnonimizacaoLead(
        id=1,
        organizacao_id=1,
        email="fulano@example.com",
        token_hash=hash_token("token-valido"),
        expira_em=datetime.now(UTC) + timedelta(hours=1),
    )
    lead1 = _lead(id=9)
    lead2 = _lead(id=10, marca="Outra Marca")
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=solicitacao),
        FakeResult(itens=[lead1, lead2]),
    )
    resposta = TestClient(app).post("/v1/privacidade/confirmar-exclusao", json={"token": "token-valido"})

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert corpo["registros_afetados"] == 2
    for lead in (lead1, lead2):
        assert lead.nome.startswith("Titular anonimizado")
        assert lead.email == f"anonimizado-{lead.id}@removido.zeregistra.local"
        assert lead.telefone == ""
        assert lead.documento is None
        assert lead.empresa is None
        assert lead.notas is None
        assert lead.aceite_marketing is False
        assert lead.anonimizado_em is not None
    assert solicitacao.usado_em is not None
    assert solicitacao.leads_anonimizados == 2

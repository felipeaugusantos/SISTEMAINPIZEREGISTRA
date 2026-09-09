from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.crm import (
    registrar_consentimento_operador,
    registrar_consentimento_prospeccao_comercial,
    registrar_consentimento_titular,
)
from app.database import get_session
from app.main import app
from app.models import ArquivoClientePortal, EventoAuditoria, Lead, Organizacao, SolicitacaoAnonimizacaoLead, StatusLead
from tests.conftest import FakeResult, FakeSession, auth_override, sessao_override, usuario_teste


@pytest.fixture(autouse=True)
def _limpar_overrides():
    yield
    app.dependency_overrides.clear()

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


def test_registrar_consentimento_prospeccao_comercial_usa_base_legal_propria() -> None:
    # Achado FASE5-4/6 da auditoria (04/09/2026): converter_prospect_em_lead
    # usava registrar_consentimento_operador ("interesse_legitimo_atendimento"),
    # que afirma que o CONTATO pediu atendimento -- falso para um Prospect
    # (dado de origem RFB, contatado por iniciativa nossa, cold prospecting).
    lead = _lead()
    registrar_consentimento_prospeccao_comercial(lead, operador_id=7)
    assert lead.consentimento_base_legal == "interesse_legitimo_prospeccao_comercial"
    assert lead.consentimento_base_legal != "interesse_legitimo_atendimento"
    assert lead.consentimento_versao_termo is None
    assert lead.consentimento_registrado_por == 7
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


# --- Achado FASE6-14 da auditoria (04/09/2026): descarte seguro por
# retenção -- antes o alerta de retenção só avisava, sem nenhum jeito de um
# humano confirmar o descarte (nem apagar o arquivo físico, não só a linha
# do banco). ---


def _sessao_admin(*resultados: FakeResult, objetos_get: list = None) -> FakeSession:
    session = FakeSession(list(resultados), objetos_get=objetos_get)

    async def _gen():
        yield session

    usuario = usuario_teste(perfil="administrador")
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _gen
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return session


def test_descartar_lead_inexistente_retorna_404() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).post(
        "/v1/admin/privacidade/leads/9/descartar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 404


def test_descartar_lead_ja_anonimizado_e_idempotente() -> None:
    lead = _lead(anonimizado_em=datetime.now(UTC))
    _sessao_admin(FakeResult(scalar=lead))

    resposta = TestClient(app).post(
        "/v1/admin/privacidade/leads/9/descartar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 200
    assert resposta.json()["ja_processado"] is True


def test_descartar_lead_dentro_do_prazo_de_retencao_retorna_422() -> None:
    lead = _lead(criado_em=datetime.now(UTC) - timedelta(days=5), anonimizado_em=None)
    org = Organizacao(id=1, retencao_dados_dias=365)
    session = _sessao_admin(FakeResult(scalar=lead), objetos_get=[org])

    resposta = TestClient(app).post(
        "/v1/admin/privacidade/leads/9/descartar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422
    assert session.adicionados == []


def test_descartar_lead_vencido_anonimiza_e_apaga_arquivos(monkeypatch: pytest.MonkeyPatch) -> None:
    caminhos_apagados = []
    monkeypatch.setattr("app.api.privacidade.delete_object", lambda caminho: caminhos_apagados.append(caminho))

    async def prazo_vigente(*_args, **_kwargs):
        return 365

    async def sem_bloqueios(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.api.privacidade.prazo_retencao_vigente", prazo_vigente)
    monkeypatch.setattr("app.api.privacidade.motivos_bloqueio_lead", sem_bloqueios)
    lead = _lead(
        criado_em=datetime.now(UTC) - timedelta(days=400),
        anonimizado_em=None,
        status=StatusLead.DESCARTADO,
    )
    org = Organizacao(id=1, retencao_dados_dias=365)
    arquivo = ArquivoClientePortal(
        id=1, organizacao_id=1, lead_id=9, cliente_id=1, nome="comprovante.pdf",
        caminho="data/uploads/comprovante.pdf", content_type="application/pdf", tamanho=100,
    )
    session = _sessao_admin(
        FakeResult(scalar=lead), FakeResult(itens=[arquivo]), objetos_get=[org]
    )

    resposta = TestClient(app).post(
        "/v1/admin/privacidade/leads/9/descartar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo == {"anonimizado": True, "arquivos_removidos": 1, "ja_processado": False}
    assert caminhos_apagados == ["data/uploads/comprovante.pdf"]
    assert session.deletados == [arquivo]
    assert lead.anonimizado_em is not None
    auditorias = [item for item in session.adicionados if isinstance(item, EventoAuditoria)]
    assert auditorias and auditorias[-1].sucesso is True


@pytest.mark.parametrize("motivo", ["processo_ativo", "contrato_ativo", "documento_obrigacao_juridica", "legal_hold"])
def test_descartar_lead_bloqueado_retorna_409(monkeypatch: pytest.MonkeyPatch, motivo: str) -> None:
    async def prazo_vigente(*_args, **_kwargs):
        return 365

    async def bloqueado(*_args, **_kwargs):
        return [motivo]

    monkeypatch.setattr("app.api.privacidade.prazo_retencao_vigente", prazo_vigente)
    monkeypatch.setattr("app.api.privacidade.motivos_bloqueio_lead", bloqueado)
    lead = _lead(
        criado_em=datetime.now(UTC) - timedelta(days=400),
        anonimizado_em=None,
        status=StatusLead.DESCARTADO,
    )
    org = Organizacao(id=1, retencao_dados_dias=365)
    session = _sessao_admin(FakeResult(scalar=lead), objetos_get=[org])

    resposta = TestClient(app).post(
        "/v1/admin/privacidade/leads/9/descartar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 409
    assert motivo in resposta.json()["detail"]["bloqueios"]
    assert lead.anonimizado_em is None
    assert session.commits == 0


def test_falha_ao_remover_arquivo_nao_anonimiza_e_audita(monkeypatch: pytest.MonkeyPatch) -> None:
    async def prazo_vigente(*_args, **_kwargs):
        return 365

    async def sem_bloqueios(*_args, **_kwargs):
        return []

    def falhar(_caminho: str) -> None:
        raise OSError("falha simulada")

    monkeypatch.setattr("app.api.privacidade.prazo_retencao_vigente", prazo_vigente)
    monkeypatch.setattr("app.api.privacidade.motivos_bloqueio_lead", sem_bloqueios)
    monkeypatch.setattr("app.api.privacidade.delete_object", falhar)
    lead = _lead(
        criado_em=datetime.now(UTC) - timedelta(days=400),
        anonimizado_em=None,
        status=StatusLead.DESCARTADO,
    )
    org = Organizacao(id=1, retencao_dados_dias=365)
    arquivo = ArquivoClientePortal(
        id=1,
        organizacao_id=1,
        lead_id=9,
        cliente_id=1,
        nome="falha.pdf",
        caminho="data/uploads/falha.pdf",
        tamanho=100,
    )
    session = _sessao_admin(FakeResult(scalar=lead), FakeResult(itens=[arquivo]), objetos_get=[org])

    resposta = TestClient(app).post(
        "/v1/admin/privacidade/leads/9/descartar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 503
    assert lead.anonimizado_em is None
    assert session.deletados == []
    auditorias = [item for item in session.adicionados if isinstance(item, EventoAuditoria)]
    assert auditorias and auditorias[-1].sucesso is False
    assert auditorias[-1].detalhes["erro"] == "falha_remocao_arquivo"
    assert session.commits == 1

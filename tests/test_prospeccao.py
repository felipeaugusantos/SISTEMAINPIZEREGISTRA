from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Lead, Prospect, StatusLead, StatusProspect
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

# --- Fase 1 do Radar de Prospecção (03/09/2026, docs/arquitetura-radar-prospeccao-2026-09-03.md) ---


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


def _override_session(session: FakeSession):
    async def _gen():
        yield session

    return _gen


def _sessao_admin(*resultados: FakeResult) -> FakeSession:
    session = FakeSession(list(resultados))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return session


def _prospect(**kwargs: object) -> Prospect:
    base: dict = {
        "id": 5,
        "organizacao_id": 1,
        "razao_social": "Empresa Teste Ltda",
        "nome_fantasia": None,
        "cnpj": None,
        "cnae_principal": None,
        "cnaes_secundarios": [],
        "porte": None,
        "situacao_cadastral": None,
        "data_abertura": None,
        "uf": None,
        "cidade": None,
        "telefone": "11988887777",
        "email": "empresa@teste.local",
        "site": None,
        "status": StatusProspect.NOVO.value,
        "motivo_descarte": None,
        "responsavel_id": None,
        "lead_id": None,
        "empresa_crm_id": None,
        "duplicado_de_id": None,
        "criado_em": datetime(2026, 9, 1, tzinfo=UTC),
        "atualizado_em": datetime(2026, 9, 1, tzinfo=UTC),
    }
    base.update(kwargs)
    return Prospect(**base)


PAYLOAD_BASE = {"razao_social": "Nova Empresa Ltda", "cnpj": "11222333000181", "email": "nova@empresa.local"}


def test_listar_prospects_retorna_paginado() -> None:
    prospect = _prospect()
    _sessao_admin(FakeResult(scalar=1), FakeResult(itens=[prospect]))

    resposta = TestClient(app).get("/v1/admin/prospects")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert len(corpo["itens"]) == 1
    assert corpo["itens"][0]["razao_social"] == "Empresa Teste Ltda"


def test_detalhar_prospect_inexistente_retorna_404() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).get("/v1/admin/prospects/999")

    assert resposta.status_code == 404


def test_criar_prospect_cria_novo_quando_nao_ha_correspondencia() -> None:
    session = _sessao_admin(FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=None))

    resposta = TestClient(app).post(
        "/v1/admin/prospects", json=PAYLOAD_BASE, headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["status"] == "novo"
    assert corpo["razao_social"] == "Nova Empresa Ltda"
    novos = [obj for obj in session.adicionados if isinstance(obj, Prospect)]
    assert len(novos) == 1
    assert session.commits == 1


def test_criar_prospect_duplicado_no_radar_nao_cria_novo() -> None:
    existente = _prospect(id=9, cnpj="11222333000181")
    session = _sessao_admin(FakeResult(scalar=existente))

    resposta = TestClient(app).post(
        "/v1/admin/prospects", json=PAYLOAD_BASE, headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    assert resposta.json()["id"] == 9
    assert [obj for obj in session.adicionados if isinstance(obj, Prospect)] == []


def test_criar_prospect_marca_empresa_crm_existente() -> None:
    _sessao_admin(FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=77))

    resposta = TestClient(app).post(
        "/v1/admin/prospects", json=PAYLOAD_BASE, headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    assert resposta.json()["empresa_crm_id"] == 77


def test_rejeitar_prospect_com_motivo_valido() -> None:
    prospect = _prospect(status=StatusProspect.NOVO.value)
    session = _sessao_admin(FakeResult(scalar=prospect))

    resposta = TestClient(app).patch(
        "/v1/admin/prospects/5",
        json={"status": "rejeitado", "motivo_descarte": "fora_do_perfil"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "rejeitado"
    assert corpo["motivo_descarte"] == "fora_do_perfil"
    assert session.commits == 1


def test_rejeitar_prospect_motivo_invalido_retorna_422() -> None:
    prospect = _prospect(status=StatusProspect.NOVO.value)
    _sessao_admin(FakeResult(scalar=prospect))

    resposta = TestClient(app).patch(
        "/v1/admin/prospects/5",
        json={"status": "rejeitado", "motivo_descarte": "porque_sim"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422


def test_rejeitar_prospect_ja_processado_retorna_422() -> None:
    prospect = _prospect(status=StatusProspect.CONVERTIDO_LEAD.value)
    _sessao_admin(FakeResult(scalar=prospect))

    resposta = TestClient(app).patch(
        "/v1/admin/prospects/5",
        json={"status": "rejeitado", "motivo_descarte": "fora_do_perfil"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422


def _csv_upload(conteudo: str) -> dict:
    return {"arquivo": ("prospects.csv", conteudo.encode("utf-8"), "text/csv")}


def test_importar_prospects_cria_ignora_duplicado_e_invalido() -> None:
    csv_conteudo = (
        "Razao Social;Email;Telefone\n"
        "Alpha Ltda;alpha@example.com;11988887777\n"
        ";semrazao@example.com;11966665555\n"
        "Beta Ltda;beta@example.com;11977776666\n"
    )
    existente = _prospect(id=3, email="beta@example.com")
    session = _sessao_admin(FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=existente))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/importar", files=_csv_upload(csv_conteudo), headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["total_linhas"] == 3
    assert corpo["criados"] == 1
    assert corpo["duplicados"] == 1
    assert corpo["invalidos"] == 1
    assert session.commits == 1


def test_converter_prospect_em_lead_cria_lead_novo() -> None:
    prospect = _prospect(status=StatusProspect.NOVO.value, email="empresa@teste.local", telefone="11988887777")
    session = _sessao_admin(FakeResult(scalar=prospect), FakeResult(scalar=None), FakeResult(scalar=None))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/converter-lead", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["criado_novo"] is True
    assert prospect.status == StatusProspect.CONVERTIDO_LEAD.value
    assert prospect.lead_id == corpo["lead_id"]
    leads_criados = [obj for obj in session.adicionados if isinstance(obj, Lead)]
    assert len(leads_criados) == 1
    assert leads_criados[0].origem == "prospeccao"


def test_converter_prospect_em_lead_reaproveita_lead_existente() -> None:
    prospect = _prospect(status=StatusProspect.NOVO.value, email="empresa@teste.local")
    lead_existente = Lead(id=42, organizacao_id=1, nome="Empresa Teste Ltda", email="empresa@teste.local", telefone="11988887777", marca="", status=StatusLead.NOVO)
    session = _sessao_admin(FakeResult(scalar=prospect), FakeResult(scalar=lead_existente))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/converter-lead", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo == {"lead_id": 42, "criado_novo": False}
    assert prospect.lead_id == 42
    assert [obj for obj in session.adicionados if isinstance(obj, Lead)] == []


def test_converter_prospect_sem_contato_retorna_422() -> None:
    prospect = _prospect(status=StatusProspect.NOVO.value, email=None, telefone=None)
    _sessao_admin(FakeResult(scalar=prospect))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/converter-lead", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422


def test_converter_prospect_ja_processado_retorna_422() -> None:
    prospect = _prospect(status=StatusProspect.CONVERTIDO_LEAD.value)
    _sessao_admin(FakeResult(scalar=prospect))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/converter-lead", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422

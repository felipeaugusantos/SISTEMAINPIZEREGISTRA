from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import CampanhaProspeccao, Lead, Prospect, StatusLead, StatusProspect
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


# --- Fase 2 do Radar de Prospecção (03/09/2026) -- fontes e campanhas ------


def _campanha(**kwargs: object) -> CampanhaProspeccao:
    base: dict = {
        "id": 4,
        "organizacao_id": 1,
        "nome": "Padarias em SP capital",
        "descricao": None,
        "criterios_busca": {"cnae_principal": "4711302", "uf": "SP"},
        "status": "rascunho",
        "meta_prospects": 200,
        "criado_por": "Admin Teste",
        "criado_em": datetime(2026, 9, 1, tzinfo=UTC),
        "encerrada_em": None,
    }
    base.update(kwargs)
    return CampanhaProspeccao(**base)


def test_criar_campanha() -> None:
    session = _sessao_admin()

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/campanhas",
        json={"nome": "Padarias em SP capital", "criterios_busca": {"cnae_principal": "4711302", "uf": "SP"}},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["nome"] == "Padarias em SP capital"
    assert corpo["status"] == "rascunho"
    assert corpo["criterios_busca"] == {"cnae_principal": "4711302", "uf": "SP"}
    assert session.commits == 1


def test_listar_campanhas_inclui_contagem_de_prospects_gerados() -> None:
    campanha = _campanha()
    session = _sessao_admin(FakeResult(scalar=1), FakeResult(itens=[campanha]), FakeResult(itens=[(4, 7)]))

    resposta = TestClient(app).get("/v1/admin/prospeccao/campanhas")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["itens"][0]["prospects_gerados"] == 7
    assert len(session.executados) == 3


def test_detalhar_campanha_inexistente_retorna_404() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).get("/v1/admin/prospeccao/campanhas/999")

    assert resposta.status_code == 404


def test_coletar_campanha_enfileira_job(monkeypatch: pytest.MonkeyPatch) -> None:
    campanha = _campanha(status="rascunho")
    session = _sessao_admin(FakeResult(scalar=campanha))

    class RedisFalso:
        def __init__(self) -> None:
            self.jobs: list[str] = []

        async def set(self, *_args: object, **_kwargs: object) -> bool:
            return True

        async def rpush(self, _chave: str, valor: str) -> None:
            self.jobs.append(valor)

        async def hincrby(self, *_args: object) -> int:
            return 1

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.queueing.cliente_redis", lambda: RedisFalso())

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/campanhas/4/coletar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 202
    assert campanha.status == "ativa"
    assert session.commits == 1


def test_coletar_campanha_ja_concluida_retorna_422() -> None:
    campanha = _campanha(status="concluida")
    _sessao_admin(FakeResult(scalar=campanha))

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/campanhas/4/coletar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422


# --- Fase 3 do Radar de Prospecção (03/09/2026) -- enriquecimento ----------


def test_enriquecer_prospect_sem_verificacao_recente_enfileira_job(monkeypatch: pytest.MonkeyPatch) -> None:
    prospect = _prospect(status=StatusProspect.NOVO.value, site="exemplo.com.br")
    session = _sessao_admin(FakeResult(scalar=prospect), FakeResult(scalar=None))

    class RedisFalso:
        async def set(self, *_args: object, **_kwargs: object) -> bool:
            return True

        async def rpush(self, *_args: object) -> None:
            return None

        async def hincrby(self, *_args: object) -> int:
            return 1

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.queueing.cliente_redis", lambda: RedisFalso())

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/enriquecer", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 202
    corpo = resposta.json()
    assert corpo["cache"] is False
    assert session.commits == 1


def test_enriquecer_prospect_com_verificacao_recente_usa_cache() -> None:
    from app.models import ProspectEnriquecimento

    prospect = _prospect(status=StatusProspect.NOVO.value, site="exemplo.com.br")
    enriquecimento_recente = ProspectEnriquecimento(
        id=1,
        organizacao_id=1,
        prospect_id=5,
        provedor="verificacao_site",
        tipo="presenca_digital",
        payload={"ativo": True, "status_code": 200, "url_final": "https://exemplo.com.br", "erro": None},
        sucesso=True,
        criado_em=datetime.now(UTC),
    )
    session = _sessao_admin(FakeResult(scalar=prospect), FakeResult(scalar=enriquecimento_recente))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/enriquecer", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 202
    corpo = resposta.json()
    assert corpo["cache"] is True
    assert corpo["resultado"]["ativo"] is True
    assert session.commits == 0


# --- Fase 4 do Radar de Prospecção (03/09/2026) -- triagem de marca --------


def test_triar_marca_prospect_enfileira_job(monkeypatch: pytest.MonkeyPatch) -> None:
    prospect = _prospect(status=StatusProspect.NOVO.value)
    session = _sessao_admin(FakeResult(scalar=prospect))

    class RedisFalso:
        async def set(self, *_args: object, **_kwargs: object) -> bool:
            return True

        async def rpush(self, *_args: object) -> None:
            return None

        async def hincrby(self, *_args: object) -> int:
            return 1

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.queueing.cliente_redis", lambda: RedisFalso())

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/triar-marca", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 202
    assert session.commits == 1


def test_listar_triagens_inclui_disclaimer() -> None:
    from app.models import ProspectTriagem
    from app.prospeccao_triagem import DISCLAIMER_TRIAGEM

    prospect = _prospect()
    triagem = ProspectTriagem(
        id=1,
        organizacao_id=1,
        prospect_id=5,
        marca_pesquisada="Empresa Teste",
        classificacao="nao_localizado",
        justificativa='Nenhuma ocorrência encontrada na base de marcas para "Empresa Teste".',
        total_resultados=0,
        criado_em=datetime(2026, 9, 1, tzinfo=UTC),
    )
    _sessao_admin(FakeResult(scalar=prospect), FakeResult(itens=[triagem]))

    resposta = TestClient(app).get("/v1/admin/prospects/5/triagens")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["disclaimer"] == DISCLAIMER_TRIAGEM
    assert corpo["itens"][0]["classificacao"] == "nao_localizado"
    # Garantia central: a resposta da API nunca pode conter um rótulo de disponibilidade.
    texto_completo = str(corpo).lower()
    assert "dispon" not in texto_completo

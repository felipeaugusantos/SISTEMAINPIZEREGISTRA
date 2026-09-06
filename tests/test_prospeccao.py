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
    # FASE5-5 (04/09/2026): _criar_prospect agora checa a lista de supressão
    # antes de tudo -- 1º resultado é essa checagem (None = não suprimido).
    session = _sessao_admin(
        FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=None)
    )

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
    session = _sessao_admin(FakeResult(scalar=None), FakeResult(scalar=existente))

    resposta = TestClient(app).post(
        "/v1/admin/prospects", json=PAYLOAD_BASE, headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    assert resposta.json()["id"] == 9
    assert [obj for obj in session.adicionados if isinstance(obj, Prospect)] == []


def test_criar_prospect_marca_empresa_crm_existente() -> None:
    _sessao_admin(
        FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=77)
    )

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
    # FASE5-5 (04/09/2026): cada linha válida agora checa supressão antes do
    # dedup -- Alpha: suprimido(None), duplicado(None), lead_id(None) (sem
    # cnpj, empresa_crm não é consultado). Beta: suprimido(None), duplicado
    # já casa com `existente` (encerra ali, sem consultar lead/empresa_crm).
    session = _sessao_admin(
        FakeResult(scalar=None),
        FakeResult(scalar=None),
        FakeResult(scalar=None),
        FakeResult(scalar=None),
        FakeResult(scalar=existente),
    )

    resposta = TestClient(app).post(
        "/v1/admin/prospects/importar", files=_csv_upload(csv_conteudo), headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["total_linhas"] == 3
    assert corpo["criados"] == 1
    assert corpo["duplicados"] == 1
    assert corpo["invalidos"] == 1
    assert corpo["suprimidos"] == 0
    assert session.commits == 1


def test_converter_prospect_em_lead_cria_lead_novo() -> None:
    prospect = _prospect(status=StatusProspect.APROVADO.value, email="empresa@teste.local", telefone="11988887777")
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
    prospect = _prospect(status=StatusProspect.APROVADO.value, email="empresa@teste.local")
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
    prospect = _prospect(status=StatusProspect.APROVADO.value, email=None, telefone=None)
    _sessao_admin(FakeResult(scalar=prospect))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/converter-lead", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422


# --- Achado FASE5-9 da auditoria (04/09/2026): a conversao aceitava status
# "novo" direto, sem NUNCA ter passado por aprovacao manual ou automatica
# auditada. ---


def test_converter_prospect_novo_sem_aprovacao_retorna_422() -> None:
    prospect = _prospect(status=StatusProspect.NOVO.value, email="empresa@teste.local", telefone="11988887777")
    _sessao_admin(FakeResult(scalar=prospect))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/converter-lead", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422
    assert "aprovado" in resposta.json()["detail"].lower()


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


def test_coletar_campanha_concluida_enfileira_nova_execucao(monkeypatch: pytest.MonkeyPatch) -> None:
    encerrada_em = datetime(2026, 9, 4, 12, 22, tzinfo=UTC)
    campanha = _campanha(status="concluida", encerrada_em=encerrada_em)
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

    redis = RedisFalso()
    monkeypatch.setattr("app.queueing.cliente_redis", lambda: redis)

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/campanhas/4/coletar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 202
    assert campanha.status == "ativa"
    assert campanha.encerrada_em is None
    assert session.commits == 1
    assert len(redis.jobs) == 1
    assert encerrada_em.isoformat() in redis.jobs[0]


def test_coletar_campanha_ativa_retorna_422() -> None:
    campanha = _campanha(status="ativa")
    _sessao_admin(FakeResult(scalar=campanha))

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/campanhas/4/coletar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422


# --- Excluir campanha (04/09/2026) -- pedido do usuario, campanha em
# rascunho esquecida sem forma de remover da lista. ---


def test_excluir_campanha_rascunho() -> None:
    campanha = _campanha(status="rascunho")
    session = _sessao_admin(FakeResult(scalar=campanha))

    resposta = TestClient(app).delete(
        "/v1/admin/prospeccao/campanhas/4", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 204
    assert campanha in session.deletados


def test_excluir_campanha_ativa_retorna_422() -> None:
    campanha = _campanha(status="ativa")
    _sessao_admin(FakeResult(scalar=campanha))

    resposta = TestClient(app).delete(
        "/v1/admin/prospeccao/campanhas/4", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422


def test_excluir_campanha_inexistente_retorna_404() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).delete(
        "/v1/admin/prospeccao/campanhas/999", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 404


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


# --- Fase 5 do Radar de Prospecção (03/09/2026) -- score e aprovação -------


def test_calcular_score_enfileira_job(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "/v1/admin/prospects/5/calcular-score", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 202
    assert session.commits == 1


def test_aprovar_prospect_novo() -> None:
    prospect = _prospect(status=StatusProspect.NOVO.value)
    session = _sessao_admin(FakeResult(scalar=prospect))

    resposta = TestClient(app).post("/v1/admin/prospects/5/aprovar", headers={"X-CSRF-Token": "csrf-teste"})

    assert resposta.status_code == 200
    assert resposta.json()["status"] == "aprovado"
    assert prospect.status == "aprovado"
    assert session.commits == 1


def test_aprovar_prospect_ja_processado_retorna_422() -> None:
    prospect = _prospect(status=StatusProspect.REJEITADO.value)
    _sessao_admin(FakeResult(scalar=prospect))

    resposta = TestClient(app).post("/v1/admin/prospects/5/aprovar", headers={"X-CSRF-Token": "csrf-teste"})

    assert resposta.status_code == 422


def test_converter_prospect_aprovado_tambem_e_permitido() -> None:
    prospect = _prospect(status=StatusProspect.APROVADO.value, email="empresa@teste.local")
    _sessao_admin(FakeResult(scalar=prospect), FakeResult(scalar=None), FakeResult(scalar=None))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/converter-lead", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201


def test_consultar_politica_prospeccao_sem_registro_devolve_default() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).get("/v1/admin/prospeccao/politica")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["aprovacao_automatica_ativa"] is False
    assert corpo["score_minimo_aprovacao"] is None


def test_editar_politica_prospeccao_exige_score_minimo_quando_ativa() -> None:
    _sessao_admin()

    resposta = TestClient(app).put(
        "/v1/admin/prospeccao/politica",
        json={"aprovacao_automatica_ativa": True},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422


def test_editar_politica_prospeccao_cria_registro_novo() -> None:
    session = _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).put(
        "/v1/admin/prospeccao/politica",
        json={"aprovacao_automatica_ativa": True, "score_minimo_aprovacao": 70},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["aprovacao_automatica_ativa"] is True
    assert corpo["score_minimo_aprovacao"] == 70
    assert session.commits == 1


def test_dashboard_prospeccao_retorna_funil_e_serie() -> None:
    _sessao_admin(FakeResult(itens=[("novo", 3), ("aprovado", 1)]), FakeResult(itens=[]))

    resposta = TestClient(app).get("/v1/admin/prospeccao/dashboard?dias=7")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["funil"] == {"novo": 3, "aprovado": 1}
    assert len(corpo["serie"]) == 8


# --- Importação do cache nacional de empresas (CNPJ/RFB) -- superadmin ----


def _sessao_superadmin(*resultados: FakeResult) -> FakeSession:
    session = FakeSession(list(resultados))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    object.__setattr__(usuario, "superadmin", True)
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return session


def _importacao_cnpj_rfb(**kwargs: object):
    from app.models import ImportacaoCnpjRfb

    base: dict = {
        "id": 1,
        "status": "concluido",
        "periodo": "2026-08",
        "etapa_atual": "Concluído",
        "total_processados": 100,
        "total_validos": 90,
        "erro": None,
        "solicitado_por": "Admin Teste",
        "solicitado_em": datetime.now(UTC),
        "concluido_em": datetime.now(UTC),
    }
    base.update(kwargs)
    return ImportacaoCnpjRfb(**base)


def test_disparar_importacao_cnpj_rfb_enfileira_job(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _sessao_superadmin(FakeResult(scalar=None))

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
        "/v1/admin/prospeccao/importar-cnpj-rfb", json={}, headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 202
    corpo = resposta.json()
    assert corpo["status"] == "executando"
    assert session.commits == 1


def test_disparar_importacao_bloqueia_quando_ja_em_andamento() -> None:
    existente = _importacao_cnpj_rfb(status="executando")
    _sessao_superadmin(FakeResult(scalar=existente))

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/importar-cnpj-rfb", json={}, headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422


def test_disparar_importacao_exige_superadmin() -> None:
    _sessao_admin()

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/importar-cnpj-rfb", json={}, headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 403


# --- Fase 5 do Radar de Prospecção (04/09/2026) -- opt-out (FASE5-5) -------


def test_criar_prospect_suprimido_retorna_422_e_nao_cria() -> None:
    from app.models import SupressaoProspeccao

    supressao = SupressaoProspeccao(
        id=1, organizacao_id=1, cnpj="11222333000181", email=None, motivo=None, criado_por="Admin",
        criado_em=datetime(2026, 9, 4, tzinfo=UTC),
    )
    session = _sessao_admin(FakeResult(scalar=supressao.id))

    resposta = TestClient(app).post(
        "/v1/admin/prospects", json=PAYLOAD_BASE, headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422
    assert "supress" in resposta.json()["detail"].lower()
    assert [obj for obj in session.adicionados if isinstance(obj, Prospect)] == []


def test_importar_prospects_conta_suprimidos_separado_de_duplicados() -> None:
    csv_conteudo = "Razao Social;Email\nAlpha Ltda;alpha@example.com\n"
    # 1ª consulta = checagem de supressão -> casa (id != None) -> linha vira "suprimido".
    session = _sessao_admin(FakeResult(scalar=1))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/importar", files=_csv_upload(csv_conteudo), headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["criados"] == 0
    assert corpo["duplicados"] == 0
    assert corpo["suprimidos"] == 1
    assert [obj for obj in session.adicionados if isinstance(obj, Prospect)] == []


def test_criar_supressao_rejeita_e_anonimiza_prospects_existentes() -> None:
    prospect_existente = _prospect(id=42, cnpj="11222333000181", status=StatusProspect.NOVO.value)
    session = _sessao_admin(FakeResult(itens=[prospect_existente]))

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/supressoes",
        json={"cnpj": "11.222.333/0001-81", "motivo": "pedido do titular"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["cnpj"] == "11222333000181"
    assert prospect_existente.status == StatusProspect.REJEITADO.value
    assert prospect_existente.motivo_descarte == "opt_out_lgpd"
    assert prospect_existente.email is None
    assert prospect_existente.telefone is None
    assert session.commits == 1


def test_criar_supressao_nao_reabre_prospect_ja_convertido_em_lead() -> None:
    prospect_convertido = _prospect(
        id=43, cnpj="11222333000181", status=StatusProspect.CONVERTIDO_LEAD.value, lead_id=9
    )
    session = _sessao_admin(FakeResult(itens=[prospect_convertido]))

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/supressoes",
        json={"cnpj": "11222333000181"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 201
    # Já virou Lead -- não regride o status do Prospect, mas ainda apaga os
    # dados de contato dele (o Lead segue seu próprio fluxo de exclusão,
    # ver app/api/privacidade.py).
    assert prospect_convertido.status == StatusProspect.CONVERTIDO_LEAD.value
    assert prospect_convertido.email is None
    assert session.commits == 1


def test_criar_supressao_sem_cnpj_nem_email_retorna_422() -> None:
    _sessao_admin()

    resposta = TestClient(app).post(
        "/v1/admin/prospeccao/supressoes", json={}, headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422


def test_listar_supressoes_prospeccao() -> None:
    from app.models import SupressaoProspeccao

    item = SupressaoProspeccao(
        id=1, organizacao_id=1, cnpj="11222333000181", email=None, motivo="pedido do titular",
        criado_por="Admin", criado_em=datetime(2026, 9, 4, tzinfo=UTC),
    )
    _sessao_admin(FakeResult(itens=[item]))

    resposta = TestClient(app).get("/v1/admin/prospeccao/supressoes")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert len(corpo) == 1
    assert corpo[0]["cnpj"] == "11222333000181"


def test_remover_supressao_prospeccao() -> None:
    from app.models import SupressaoProspeccao

    item = SupressaoProspeccao(
        id=1, organizacao_id=1, cnpj="11222333000181", email=None, motivo=None,
        criado_por="Admin", criado_em=datetime(2026, 9, 4, tzinfo=UTC),
    )
    session = _sessao_admin(FakeResult(scalar=item))

    resposta = TestClient(app).delete(
        "/v1/admin/prospeccao/supressoes/1", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 204
    assert session.deletados == [item]


def test_remover_supressao_inexistente_retorna_404() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).delete(
        "/v1/admin/prospeccao/supressoes/999", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 404


def test_listar_importacoes_cnpj_rfb() -> None:
    execucao = _importacao_cnpj_rfb()
    _sessao_admin(FakeResult(itens=[execucao]))

    resposta = TestClient(app).get("/v1/admin/prospeccao/importar-cnpj-rfb")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo[0]["status"] == "concluido"
    assert corpo[0]["total_validos"] == 90


# --- Fase 1 do roadmap pos-auditoria do CRM (06/09/2026): central de
# duplicidades e mesclagem assistida. Prospect.duplicado_de_id e
# StatusProspect.DUPLICADO existiam desde 03/09/2026 mas nenhum codigo os
# escrevia (achado da auditoria completa do CRM). ---


def test_listar_duplicatas_agrupa_por_cnpj() -> None:
    duplicado_a = _prospect(id=10, cnpj="11222333000181", email=None, telefone=None)
    duplicado_b = _prospect(id=11, cnpj="11222333000181", email=None, telefone=None)
    session = _sessao_admin(
        FakeResult(itens=[("11222333000181", duplicado_a), ("11222333000181", duplicado_b)]),
        FakeResult(itens=[]),
        FakeResult(itens=[]),
    )

    resposta = TestClient(app).get("/v1/admin/prospects/duplicatas")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert len(corpo["grupos"]) == 1
    assert corpo["grupos"][0]["criterio"] == "cnpj"
    assert corpo["grupos"][0]["valor"] == "11222333000181"
    assert {item["id"] for item in corpo["grupos"][0]["itens"]} == {10, 11}
    assert session.commits == 0


def test_listar_duplicatas_sem_nenhuma_retorna_vazio() -> None:
    _sessao_admin(FakeResult(itens=[]), FakeResult(itens=[]), FakeResult(itens=[]))

    resposta = TestClient(app).get("/v1/admin/prospects/duplicatas")

    assert resposta.status_code == 200
    assert resposta.json()["grupos"] == []


def test_mesclar_prospect_preenche_campos_vazios_e_marca_duplicado() -> None:
    primario = _prospect(id=5, telefone=None, email=None, uf=None)
    duplicado = _prospect(id=6, telefone="11988887777", email="duplicado@teste.local", uf="SP")
    session = _sessao_admin(
        FakeResult(scalar=primario),
        FakeResult(scalar=duplicado),
        FakeResult(),  # UPDATE prospect_triagens
        FakeResult(),  # UPDATE prospect_enriquecimentos
        FakeResult(),  # UPDATE historico_status_prospect
    )

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/mesclar",
        json={"duplicado_id": 6},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["telefone"] == "11988887777"
    assert corpo["email"] == "duplicado@teste.local"
    assert corpo["uf"] == "SP"
    assert duplicado.status == "duplicado"
    assert duplicado.duplicado_de_id == 5
    assert session.commits == 1
    assert any(
        getattr(item, "status", None) == "duplicado" and getattr(item, "prospect_id", None) == 6
        for item in session.adicionados
    )


def test_mesclar_prospect_nao_sobrescreve_campo_ja_preenchido() -> None:
    primario = _prospect(id=5, telefone="11900000000")
    duplicado = _prospect(id=6, telefone="11988887777")
    _sessao_admin(
        FakeResult(scalar=primario),
        FakeResult(scalar=duplicado),
        FakeResult(),
        FakeResult(),
        FakeResult(),
    )

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/mesclar",
        json={"duplicado_id": 6},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    assert resposta.json()["telefone"] == "11900000000"


def test_mesclar_prospect_com_ele_mesmo_retorna_422() -> None:
    _sessao_admin()

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/mesclar",
        json={"duplicado_id": 5},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422


def test_mesclar_prospect_ja_convertido_em_lead_retorna_422() -> None:
    primario = _prospect(id=5)
    duplicado = _prospect(id=6, status=StatusProspect.CONVERTIDO_LEAD.value, lead_id=42)
    _sessao_admin(FakeResult(scalar=primario), FakeResult(scalar=duplicado))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/mesclar",
        json={"duplicado_id": 6},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422


def test_mesclar_prospect_ja_mesclado_retorna_422() -> None:
    primario = _prospect(id=5)
    duplicado = _prospect(id=6, status=StatusProspect.DUPLICADO.value, duplicado_de_id=99)
    _sessao_admin(FakeResult(scalar=primario), FakeResult(scalar=duplicado))

    resposta = TestClient(app).post(
        "/v1/admin/prospects/5/mesclar",
        json={"duplicado_id": 6},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422

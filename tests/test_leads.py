from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.juridico import FUSO_BRASIL
from app.api.leads import (
    MoverPesquisaInput,
    _lead_response,
    _resumir_alteracoes,
    _resumo_pesquisa,
    _valor_csv,
    dashboard_funil_produtividade,
    limitar_acoes_admin,
    limitar_admin,
    limitar_leads,
)
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import (
    EventoAuditoria,
    Lead,
    PesquisaMarca,
    QualificacaoIALead,
    RespostaEmailLead,
    StatusLead,
    SugestaoIALead,
    UsuarioOperacoes,
    VersaoRelatorioMarca,
)
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


# --- Achado L1/L2/L3 do plano Leads/CRM (03/09/2026): upsert público sobrescrevia
# marca/origem de um lead existente quando a mesma pessoa voltava interessada em
# outra marca, perdendo o histórico da oportunidade anterior. ---


def _lead_existente(**kwargs: object) -> Lead:
    base: dict = {
        "id": 7,
        "organizacao_id": 1,
        "nome": "Fulano de Tal",
        "email": "fulano@example.com",
        "telefone": "11999998888",
        "documento": "12345678900",
        "empresa": "Fulano Comércio",
        "marca": "ACME",
        "origem": "resultados",
        "status": StatusLead.QUALIFICADO,
    }
    base.update(kwargs)
    return Lead(**base)


def test_upsert_publico_mesma_marca_atualiza_lead_existente() -> None:
    lead = _lead_existente()
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/leads", json=_payload(marca="acme"))
    assert resposta.status_code == 201
    assert resposta.json()["id"] == 7
    assert lead.marca == "acme"


def test_upsert_publico_nao_sobrescreve_a_origem_original_do_lead() -> None:
    """Achado CRM-5 da auditoria (04/09/2026): a origem do lead virava a do
    reenvio (aqui "processo"), perdendo de onde a oportunidade realmente
    veio na primeira vez ("resultados", ver _lead_existente). O last-touch
    de UTM já tinha proteção equivalente -- só a origem estava sem."""
    lead = _lead_existente()
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/leads", json=_payload(marca="acme", origem="processo"))
    assert resposta.status_code == 201
    assert lead.origem == "resultados"


def test_upsert_publico_mascara_documento_mesmo_atualizando_no_lugar() -> None:
    # Vazamento pré-existente descoberto ao mexer nesta função: o reenvio do
    # formulário público devolvia o documento (CPF/CNPJ) do lead em claro.
    lead = _lead_existente()
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/leads", json=_payload(marca="acme"))
    assert resposta.json()["documento"] == "***8900"


def test_upsert_publico_marca_vazia_atualiza_lead_existente() -> None:
    lead = _lead_existente()
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    payload = _payload()
    del payload["marca"]
    resposta = TestClient(app).post("/v1/leads", json=payload)
    assert resposta.status_code == 201
    assert resposta.json()["id"] == 7
    assert lead.marca == "ACME"


def test_upsert_publico_marca_diferente_cria_novo_lead_e_preserva_o_antigo() -> None:
    lead = _lead_existente()
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/leads", json=_payload(marca="Outra Marca Ltda"))

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["id"] != 7
    assert corpo["marca"] == "Outra Marca Ltda"
    # o lead antigo não pode ser tocado -- é a própria regressão que este achado corrige.
    assert lead.marca == "ACME"
    assert lead.origem == "resultados"


def test_upsert_publico_marca_diferente_herda_documento_e_empresa_do_contato() -> None:
    lead = _lead_existente()
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/leads", json=_payload(marca="Outra Marca Ltda"))
    corpo = resposta.json()
    # documento é PII sensível (CPF/CNPJ) que o formulário público nunca coletou
    # nesta submissão -- não pode vazar em claro na resposta anônima.
    assert corpo["documento"] == "***8900"
    assert corpo["empresa"] == "Fulano Comércio"


# --- Achado L13 do plano Leads/CRM (03/09/2026): consentimento estruturado ---


def test_upsert_publico_registra_consentimento_do_titular_em_lead_novo() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/leads", json=_payload())
    assert resposta.status_code == 201


def test_upsert_publico_atualiza_consentimento_em_lead_existente() -> None:
    lead = _lead_existente()
    assert lead.consentimento_base_legal is None
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/leads", json=_payload(marca="acme"))
    assert resposta.status_code == 201
    assert lead.consentimento_base_legal == "consentimento_titular"
    assert lead.consentimento_versao_termo == "1.0"
    assert lead.consentimento_em is not None


# --- Achado L2 do plano Leads/CRM (03/09/2026): captura de UTM ---


def test_upsert_publico_captura_utm_de_primeira_e_ultima_origem_em_lead_novo() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    resposta = TestClient(app).post(
        "/v1/leads",
        json=_payload(utm_source="google", utm_medium="cpc", utm_campaign="marca-institucional"),
    )
    assert resposta.status_code == 201


def test_upsert_publico_preserva_primeira_utm_e_atualiza_a_ultima() -> None:
    lead = _lead_existente()
    lead.utm_source = "google"
    lead.utm_medium = "cpc"
    lead.utm_campaign = "campanha-1"
    lead.utm_source_ultimo = "google"
    lead.utm_medium_ultimo = "cpc"
    lead.utm_campaign_ultimo = "campanha-1"
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))

    resposta = TestClient(app).post(
        "/v1/leads",
        json=_payload(marca="acme", utm_source="instagram", utm_medium="social", utm_campaign="campanha-2"),
    )

    assert resposta.status_code == 201
    # primeiro touch nunca muda -- é a atribuição de origem da oportunidade.
    assert lead.utm_source == "google"
    assert lead.utm_medium == "cpc"
    assert lead.utm_campaign == "campanha-1"
    # último touch reflete o reenvio mais recente.
    assert lead.utm_source_ultimo == "instagram"
    assert lead.utm_medium_ultimo == "social"
    assert lead.utm_campaign_ultimo == "campanha-2"


def test_upsert_publico_sem_utm_no_reenvio_preserva_a_ultima_utm_conhecida() -> None:
    lead = _lead_existente()
    lead.utm_source_ultimo = "google"
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))

    resposta = TestClient(app).post("/v1/leads", json=_payload(marca="acme"))

    assert resposta.status_code == 201
    assert lead.utm_source_ultimo == "google"


# --- Achado P0 da auditoria de Leads (03/09/2026): telefone com máscara
# diferente virava lead duplicado; nenhum alerta avisava a equipe de um lead
# novo sem responsável. ---


def _override_session(session: FakeSession):
    async def _gen():
        yield session

    return _gen


def test_upsert_publico_normaliza_telefone_na_query_de_duplicidade() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    app.dependency_overrides[get_session] = _override_session(session)

    resposta = TestClient(app).post("/v1/leads", json=_payload(telefone="(16) 99999-9999"))

    assert resposta.status_code == 201
    primeira_consulta = str(session.executados[0].compile(compile_kwargs={"literal_binds": True}))
    assert "regexp_replace" in primeira_consulta
    assert "16999999999" in primeira_consulta


def test_upsert_publico_telefone_com_mascara_diferente_reconhece_o_mesmo_lead() -> None:
    lead = _lead_existente(telefone="(16) 99999-9999")
    session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=None)])
    app.dependency_overrides[get_session] = _override_session(session)

    resposta = TestClient(app).post(
        "/v1/leads", json=_payload(telefone="16999999999", marca="acme", email="outro@example.com")
    )

    assert resposta.status_code == 201
    assert resposta.json()["id"] == lead.id


async def _sem_envio(*_a: object, **_k: object) -> None:
    return None


def test_upsert_publico_novo_lead_sem_responsavel_dispara_alerta() -> None:
    import app.api.leads as modulo

    chamadas: list[tuple] = []

    async def _capturar(*args: object) -> None:
        chamadas.append(args)

    original = modulo.enviar_alerta_novo_lead
    modulo.enviar_alerta_novo_lead = _capturar
    try:
        session = FakeSession([FakeResult(scalar=None)])
        app.dependency_overrides[get_session] = _override_session(session)
        resposta = TestClient(app).post("/v1/leads", json=_payload())
    finally:
        modulo.enviar_alerta_novo_lead = original

    assert resposta.status_code == 201
    assert len(chamadas) == 1
    assert chamadas[0][0] == "Fulano de Tal"


def test_upsert_publico_atualizacao_de_lead_existente_nao_dispara_alerta() -> None:
    import app.api.leads as modulo

    chamadas: list[tuple] = []

    async def _capturar(*args: object) -> None:
        chamadas.append(args)

    original = modulo.enviar_alerta_novo_lead
    modulo.enviar_alerta_novo_lead = _capturar
    try:
        lead = _lead_existente()
        session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=None)])
        app.dependency_overrides[get_session] = _override_session(session)
        resposta = TestClient(app).post("/v1/leads", json=_payload(marca="acme"))
    finally:
        modulo.enviar_alerta_novo_lead = original

    assert resposta.status_code == 201
    assert chamadas == []


def test_leads_relacionados_lista_outras_oportunidades_do_mesmo_contato() -> None:
    lead = _lead_existente(id=7)
    relacionado = _lead_existente(id=8, marca="Outra Marca")
    session = FakeSession([FakeResult(scalar=lead), FakeResult(itens=[relacionado])])
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())

    resposta = TestClient(app).get("/v1/admin/leads/7/relacionados")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["itens"][0]["id"] == 8
    assert corpo["itens"][0]["marca"] == "Outra Marca"


def test_leads_relacionados_lead_inexistente_retorna_404() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())

    resposta = TestClient(app).get("/v1/admin/leads/999/relacionados")

    assert resposta.status_code == 404


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


def test_lead_publico_nasce_com_proxima_acao_padrao() -> None:
    # Achado da auditoria do CRM: todo lead do formulário público nascia com
    # proxima_acao_em nulo. Agora sempre recebe um fallback (sem bloquear o
    # formulário do site com 422 quando a organização não configurou política).
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None), FakeResult(scalar=None))
    resposta = TestClient(app).post("/v1/leads", json=_payload())
    assert resposta.status_code == 201
    assert resposta.json()["proxima_acao_em"] is not None


def test_mover_kanban_bloqueia_oportunidade_aberta_sem_proxima_acao() -> None:
    lead = Lead(
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
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/9/kanban",
        json={"etapa": "aguardando_contato_nosso"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422
    assert "próxima ação" in resposta.json()["detail"]


def test_mover_kanban_permite_oportunidade_aberta_com_proxima_acao() -> None:
    lead = Lead(
        id=9,
        organizacao_id=1,
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        origem="processo",
        status=StatusLead.NOVO,
        fase="contato_inicial",
        responsavel_id=3,
        proxima_acao_em=datetime.now(UTC),
        aceite_marketing=False,
    )
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/9/kanban",
        json={"etapa": "aguardando_contato_nosso"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200


def test_leads_kanban_marca_card_atrasado_fora_do_sla() -> None:
    agora = datetime.now(UTC)
    lead_atrasado = Lead(
        id=1,
        organizacao_id=1,
        nome="Atrasado",
        email="atrasado@example.com",
        telefone="11999990000",
        marca="ACME",
        origem="processo",
        status=StatusLead.NOVO,
        fase="contato_inicial",
        responsavel_id=None,
        proxima_acao_em=None,
        aceite_marketing=False,
    )
    lead_atrasado.atualizado_em = agora - timedelta(hours=10)
    lead_no_prazo = Lead(
        id=2,
        organizacao_id=1,
        nome="No prazo",
        email="noprazo@example.com",
        telefone="11999990001",
        marca="ACME",
        origem="processo",
        status=StatusLead.NOVO,
        fase="contato_inicial",
        responsavel_id=None,
        proxima_acao_em=None,
        aceite_marketing=False,
    )
    lead_no_prazo.atualizado_em = agora - timedelta(hours=1)
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[lead_atrasado, lead_no_prazo]))
    usuario = usuario_teste()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).get("/v1/admin/leads-kanban")

    assert resposta.status_code == 200
    cartoes = {cartao["id"]: cartao for cartao in resposta.json()["cards"]}
    assert cartoes[1]["etapa"] == "primeiro_contato"
    assert cartoes[1]["sla_horas"] == 4
    assert cartoes[1]["atrasado"] is True
    assert cartoes[2]["atrasado"] is False


def test_leads_kanban_etapa_sem_sla_nunca_fica_atrasada() -> None:
    lead = Lead(
        id=3,
        organizacao_id=1,
        nome="Processo em andamento",
        email="processo@example.com",
        telefone="11999990002",
        marca="ACME",
        origem="processo",
        status=StatusLead.NOVO,
        fase="processo_inpi",
        responsavel_id=None,
        proxima_acao_em=None,
        aceite_marketing=False,
    )
    lead.atualizado_em = datetime.now(UTC) - timedelta(days=365)
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[lead]))
    usuario = usuario_teste()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).get("/v1/admin/leads-kanban")

    assert resposta.status_code == 200
    cartao = resposta.json()["cards"][0]
    assert cartao["sla_horas"] is None
    assert cartao["atrasado"] is False


def test_distribuir_leads_round_robin_entre_atendentes_elegiveis() -> None:
    # FakeSession não avalia o .where() do SQLAlchemy -- o FakeResult já
    # representa o resultado de "perfil == comercial" aplicado pelo banco,
    # então só entram aqui os usuários que passariam nesse filtro.
    ana = UsuarioOperacoes(id=1, organizacao_id=1, nome="Ana", perfil="comercial", ativo=True)
    beto = UsuarioOperacoes(id=2, organizacao_id=1, nome="Beto", perfil="comercial", ativo=True)

    leads = [
        Lead(
            id=10 + indice,
            organizacao_id=1,
            nome=f"Lead {indice}",
            email=f"lead{indice}@example.com",
            telefone="11900000000",
            marca="ACME",
            origem="processo",
            status=StatusLead.NOVO,
            responsavel_id=None,
        )
        for indice in range(3)
    ]
    for indice, lead in enumerate(leads):
        lead.criado_em = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=indice)

    # distribuir_leads reaproveita distribuir_lead_automaticamente (app/crm.py):
    # 1) _atendentes_elegiveis, 2) obter_politica_crm (None -> politica default),
    # 3) leads sem responsavel, 4-9) por lead: consulta de ids comerciais +
    # consulta de carga atual por responsavel (achado do usuário, 16/09/2026:
    # round-robin agora pesa pela carga, não só pelo cursor -- simulando aqui
    # a carga real que cada atribuição anterior deixaria: lead0 encontra os
    # dois zerados; lead1 já vê ana com 1 aberto (de lead0); lead2 vê os dois
    # empatados em 1, decidido pelo cursor de sempre).
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(itens=[ana, beto]),
        FakeResult(scalar=None),
        FakeResult(itens=leads),
        FakeResult(itens=[1, 2]),
        FakeResult(itens=[]),
        FakeResult(itens=[1, 2]),
        FakeResult(itens=[(1, 1)]),
        FakeResult(itens=[1, 2]),
        FakeResult(itens=[(1, 1), (2, 1)]),
    )
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/distribuir",
        json={},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["distribuidos"] == 3
    assert leads[0].responsavel_id == 1
    assert leads[1].responsavel_id == 2
    assert leads[2].responsavel_id == 1
    assert corpo["por_responsavel"] == {"Ana": 2, "Beto": 1}
    assert {item["nome"] for item in corpo["atendentes"]} == {"Ana", "Beto"}


def test_distribuir_leads_sem_atendente_elegivel_retorna_422() -> None:
    # Nenhum usuario com perfil comercial nesta organizacao.
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[]))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/distribuir",
        json={},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422


# --- Achado real (10/09/2026): pesquisas de clientes diferentes caíram no
# mesmo lead por causa de e-mail genérico reaproveitado -- correção manual
# para os casos já misturados, movendo a pesquisa sem apagar nenhum dado. ---


def test_mover_pesquisa_input_exige_um_destino() -> None:
    with pytest.raises(ValueError):
        MoverPesquisaInput()
    with pytest.raises(ValueError):
        MoverPesquisaInput(lead_id_destino=1, novo_cliente={"nome": "Fulano"})


def test_mover_pesquisa_para_lead_existente() -> None:
    pesquisa = PesquisaMarca(id="pesquisa-1", organizacao_id=1, lead_id=9, marca="ACME", tipo_pesquisa="completa")
    lead_destino = Lead(
        id=20,
        organizacao_id=1,
        nome="Cliente Certo",
        email="certo@example.com",
        telefone="11900000000",
        marca="ACME",
        origem="operador",
        status=StatusLead.NOVO,
    )
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=pesquisa),
        FakeResult(scalar=lead_destino),
    )
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/pesquisas/pesquisa-1/mover-lead",
        json={"lead_id_destino": 20},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["lead_origem_id"] == 9
    assert corpo["lead_destino_id"] == 20
    assert pesquisa.lead_id == 20


def test_mover_pesquisa_para_o_mesmo_lead_retorna_422() -> None:
    pesquisa = PesquisaMarca(id="pesquisa-1", organizacao_id=1, lead_id=9, marca="ACME", tipo_pesquisa="completa")
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=pesquisa))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/pesquisas/pesquisa-1/mover-lead",
        json={"lead_id_destino": 9},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422


def test_mover_pesquisa_cria_novo_cliente() -> None:
    pesquisa = PesquisaMarca(
        id="pesquisa-1", organizacao_id=1, lead_id=9, marca="ACME", atividade="Comércio", tipo_pesquisa="completa"
    )
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=pesquisa))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/pesquisas/pesquisa-1/mover-lead",
        json={"novo_cliente": {"nome": "Cliente Novo", "telefone": "11988887777"}},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["lead_origem_id"] == 9
    assert corpo["lead_destino_id"] != 9
    assert pesquisa.lead_id == corpo["lead_destino_id"]


def test_mover_pesquisa_sem_telefone_usa_id_da_pesquisa_para_email_unico() -> None:
    # Achado ao usar o proprio endpoint em producao (10/09/2026): sem
    # telefone, usar novo.nome como base do e-mail sintetico derrubava a
    # unicidade sempre que o nome nao tivesse nenhum digito -- dois clientes
    # sem telefone caindo no mesmo "presencial-@..." reproduziria o proprio
    # bug que este endpoint existe para corrigir. Duas pesquisas diferentes,
    # sem telefone e com o mesmo nome (sem digito), devem gerar Leads com
    # e-mails distintos (cada um usa o id da propria pesquisa, sempre unico,
    # como base do e-mail sintetico).
    pesquisa_a = PesquisaMarca(id="pesquisa-aaa", organizacao_id=1, lead_id=9, marca="ACME", tipo_pesquisa="completa")
    pesquisa_b = PesquisaMarca(id="pesquisa-bbb", organizacao_id=1, lead_id=9, marca="ACME", tipo_pesquisa="completa")
    session = FakeSession(
        [FakeResult(scalar=pesquisa_a), FakeResult(scalar=None), FakeResult(scalar=pesquisa_b), FakeResult(scalar=None)]
    )
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta_a = TestClient(app).post(
        "/v1/admin/pesquisas/pesquisa-aaa/mover-lead",
        json={"novo_cliente": {"nome": "Cliente Sem Nome Numerico"}},
        headers={"X-CSRF-Token": "csrf-teste"},
    )
    resposta_b = TestClient(app).post(
        "/v1/admin/pesquisas/pesquisa-bbb/mover-lead",
        json={"novo_cliente": {"nome": "Cliente Sem Nome Numerico"}},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta_a.status_code == 200
    assert resposta_b.status_code == 200
    novos_leads = [item for item in session.adicionados if isinstance(item, Lead)]
    assert len(novos_leads) == 2
    assert novos_leads[0].email != novos_leads[1].email


def test_restaurar_lead_com_responsavel_nao_quebra_por_missing_greenlet() -> None:
    # Achado (10/09/2026): session.refresh(lead) sozinho, seguido de acesso a
    # lead.responsavel.nome, derrubava este endpoint com MissingGreenlet
    # sempre que o lead restaurado tinha um responsavel_id definido.
    lead = _lead_existente(id=7, arquivado_em=datetime.now(UTC), responsavel_id=5)
    lead.criado_em = lead.atualizado_em = datetime.now(UTC)
    lead.responsavel = UsuarioOperacoes(id=5, organizacao_id=1, nome="Responsável Atual")
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=lead),
        FakeResult(scalar=lead),
    )
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/7/restaurar",
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    assert resposta.json()["responsavel_nome"] == "Responsável Atual"
    assert lead.arquivado_em is None


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
        isinstance(item, EventoAuditoria) and item.acao == "bloquear_relatorio" for item in sessao.adicionados
    )


# --- Achado P2 da auditoria de Leads (03/09/2026): timeline não incluía o log
# de auditoria genérico (trocas de responsável, edições de campo). ---


def test_resumir_alteracoes_formata_de_para() -> None:
    resumo = _resumir_alteracoes({"responsavel_id": {"de": None, "para": 5}, "notas_atualizadas": True})
    assert "responsavel_id: None → 5" in resumo
    assert "notas_atualizadas: True" in resumo


def test_resumir_alteracoes_vazio_retorna_none() -> None:
    assert _resumir_alteracoes({}) is None


def test_timeline_inclui_evento_de_auditoria() -> None:
    lead = _lead_existente(id=7, criado_em=datetime(2026, 8, 1, tzinfo=UTC))
    evento_auditoria = EventoAuditoria(
        organizacao_id=1,
        ator="Admin Teste",
        acao="alterar",
        recurso="lead:7",
        sucesso=True,
        status_http=200,
        detalhes={"responsavel_id": {"de": None, "para": 5}},
        criado_em=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session = FakeSession(
        [
            FakeResult(scalar=lead),  # lead
            FakeResult(itens=[]),  # fases
            FakeResult(itens=[]),  # contatos
            FakeResult(itens=[]),  # respostas_email
            FakeResult(itens=[]),  # pesquisas
            FakeResult(itens=[]),  # propostas
            FakeResult(itens=[]),  # mensagens_portal
            FakeResult(itens=[]),  # eventos_dominio
            FakeResult(itens=[]),  # documentos
            FakeResult(itens=[]),  # guias
            FakeResult(itens=[evento_auditoria]),  # auditoria
        ]
    )
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())

    resposta = TestClient(app).get("/v1/admin/leads/7/timeline")

    assert resposta.status_code == 200
    corpo = resposta.json()
    tipos = [item["tipo"] for item in corpo["eventos"]]
    assert "auditoria_alterar" in tipos
    evento = next(item for item in corpo["eventos"] if item["tipo"] == "auditoria_alterar")
    assert "responsavel_id" in evento["payload"]["detalhe"]


def test_timeline_inclui_conteudo_da_resposta_de_email() -> None:
    # Achado item 21 da auditoria completa do CRM (06/09/2026): a timeline
    # deve mostrar o que o lead escreveu na resposta, não só que respondeu.
    lead = _lead_existente(id=7, criado_em=datetime(2026, 8, 1, tzinfo=UTC))
    resposta_email = RespostaEmailLead(
        organizacao_id=1,
        lead_id=7,
        remetente="cliente@example.test",
        assunto="Re: Proposta",
        corpo="Obrigado, vou analisar com calma.",
        recebido_em=datetime(2026, 9, 2, tzinfo=UTC),
    )
    session = FakeSession(
        [
            FakeResult(scalar=lead),  # lead
            FakeResult(itens=[]),  # fases
            FakeResult(itens=[]),  # contatos
            FakeResult(itens=[resposta_email]),  # respostas_email
            FakeResult(itens=[]),  # pesquisas
            FakeResult(itens=[]),  # propostas
            FakeResult(itens=[]),  # mensagens_portal
            FakeResult(itens=[]),  # eventos_dominio
            FakeResult(itens=[]),  # documentos
            FakeResult(itens=[]),  # guias
            FakeResult(itens=[]),  # auditoria
        ]
    )
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())

    resposta = TestClient(app).get("/v1/admin/leads/7/timeline")

    assert resposta.status_code == 200
    corpo = resposta.json()
    evento = next(item for item in corpo["eventos"] if item["tipo"] == "resposta_email")
    assert "Re: Proposta" in evento["titulo"]
    assert evento["payload"]["detalhe"] == "Obrigado, vou analisar com calma."
    assert evento["payload"]["autor"] == "cliente@example.test"


# --- Achado P2 da auditoria de Leads (03/09/2026): nenhuma notificação
# avisava o novo responsável quando um lead era atribuído a ele. ---


def test_atualizar_lead_novo_responsavel_dispara_alerta() -> None:
    import app.api.leads as modulo

    chamadas: list[tuple] = []

    async def _capturar(*args: object) -> None:
        chamadas.append(args)

    original = modulo.enviar_alerta_lead_atribuido
    modulo.enviar_alerta_lead_atribuido = _capturar
    try:
        lead = _lead_existente(id=7, responsavel_id=None, proxima_acao_em=datetime.now(UTC))
        lead.criado_em = lead.atualizado_em = datetime.now(UTC)
        operador = UsuarioOperacoes(
            id=5, organizacao_id=1, nome="Novo Responsável", usuario="novo", email="novo@teste.local"
        )
        lead.responsavel = operador
        usuario = usuario_teste()
        object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
        session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=operador), FakeResult(scalar=None), FakeResult(scalar=lead)])
        app.dependency_overrides[get_session] = _override_session(session)
        app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

        resposta = TestClient(app).patch(
            "/v1/admin/leads/7",
            json={"responsavel_id": 5},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        modulo.enviar_alerta_lead_atribuido = original

    assert resposta.status_code == 200
    assert resposta.json()["responsavel_nome"] == "Novo Responsável"
    assert len(chamadas) == 1
    assert chamadas[0][1] == "novo@teste.local"


def test_atualizar_lead_mesmo_responsavel_nao_dispara_alerta() -> None:
    import app.api.leads as modulo

    chamadas: list[tuple] = []

    async def _capturar(*args: object) -> None:
        chamadas.append(args)

    original = modulo.enviar_alerta_lead_atribuido
    modulo.enviar_alerta_lead_atribuido = _capturar
    try:
        lead = _lead_existente(id=7, responsavel_id=5, proxima_acao_em=datetime.now(UTC))
        lead.criado_em = lead.atualizado_em = datetime.now(UTC)
        operador = UsuarioOperacoes(
            id=5, organizacao_id=1, nome="Mesmo Responsável", usuario="mesmo", email="mesmo@teste.local"
        )
        lead.responsavel = operador
        usuario = usuario_teste()
        object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
        session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=operador), FakeResult(scalar=None), FakeResult(scalar=lead)])
        app.dependency_overrides[get_session] = _override_session(session)
        app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

        resposta = TestClient(app).patch(
            "/v1/admin/leads/7",
            json={"responsavel_id": 5},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        modulo.enviar_alerta_lead_atribuido = original

    assert resposta.status_code == 200
    assert chamadas == []


# --- Fase 4 do roadmap pós-auditoria de Leads (03/09/2026): série temporal do
# funil e importação em massa via CSV. ---


def _sessao_admin(*resultados: FakeResult) -> FakeSession:
    session = FakeSession(list(resultados))
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return session


def test_serie_temporal_leads_agrega_criacoes_e_funil_por_dia() -> None:
    # O endpoint agrupa por dia civil de Brasília (achado D1 da auditoria de
    # 06/09/2026, app.api.leads.serie_temporal_leads), não por dia em UTC --
    # perto da virada do dia (21h-23h59 UTC = já é o dia seguinte em UTC mas
    # ainda o dia anterior em Brasília) os dois divergem. "hoje" precisa ser
    # calculado com a mesma conversão de fuso do endpoint para o teste não
    # ficar dependente do horário em que a suíte roda.
    hoje = datetime.now(UTC).astimezone(FUSO_BRASIL).date()
    ontem = hoje - timedelta(days=1)
    _sessao_admin(
        FakeResult(itens=[(ontem, 3), (hoje, 1)]),
        FakeResult(itens=[(ontem, "contato_inicial", 3), (hoje, "proposta_enviada", 1)]),
    )

    resposta = TestClient(app).get("/v1/admin/leads-dashboard/serie-temporal?dias=7")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["dias"] == 7
    assert len(corpo["serie"]) == 8
    assert corpo["serie"][-1]["data"] == hoje.isoformat()
    por_data = {dia["data"]: dia for dia in corpo["serie"]}
    assert por_data[ontem.isoformat()]["leads_criados"] == 3
    assert por_data[ontem.isoformat()]["funil"]["contato_inicial"] == 3
    assert por_data[ontem.isoformat()]["funil"]["proposta_enviada"] == 0
    assert por_data[hoje.isoformat()]["leads_criados"] == 1
    assert por_data[hoje.isoformat()]["funil"]["proposta_enviada"] == 1


def test_serie_temporal_leads_sem_dados_no_periodo_devolve_zeros() -> None:
    _sessao_admin(FakeResult(itens=[]), FakeResult(itens=[]))

    resposta = TestClient(app).get("/v1/admin/leads-dashboard/serie-temporal?dias=7")

    assert resposta.status_code == 200
    serie = resposta.json()["serie"]
    assert len(serie) == 8
    assert all(dia["leads_criados"] == 0 for dia in serie)
    assert all(all(total == 0 for total in dia["funil"].values()) for dia in serie)


def _csv_upload(conteudo: str) -> dict:
    return {"arquivo": ("leads.csv", conteudo.encode("utf-8"), "text/csv")}


def test_importar_leads_cria_novos_e_ignora_duplicado() -> None:
    csv_conteudo = (
        "Nome;Email;Telefone;Marca;Empresa;Observacoes\n"
        "Ana Silva;ana@example.com;11988887777;ACME;Ana Comércio;Cliente antigo\n"
        "Bruno Souza;bruno@example.com;11977776666;BETA;;\n"
        "Já Existe;existente@example.com;11966665555;GAMA;;\n"
    )
    session = _sessao_admin(
        FakeResult(itens=[("existente@example.com", "11966665555")]),
        FakeResult(scalar=None),
    )

    resposta = TestClient(app).post(
        "/v1/admin/leads/importar",
        files=_csv_upload(csv_conteudo),
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["total_linhas"] == 3
    assert corpo["criados"] == 2
    assert corpo["duplicados"] == 1
    assert corpo["sem_dados_essenciais"] == 0
    leads_criados = [obj for obj in session.adicionados if isinstance(obj, Lead)]
    assert len(leads_criados) == 2
    assert all(lead.origem == "importacao" for lead in leads_criados)
    assert {lead.email for lead in leads_criados} == {"ana@example.com", "bruno@example.com"}
    assert session.commits == 1


def test_importar_leads_linha_sem_contato_e_ignorada() -> None:
    csv_conteudo = "Nome;Email;Telefone\nSem Contato;;\nCom Contato;com@example.com;\n"
    session = _sessao_admin(FakeResult(itens=[]), FakeResult(scalar=None))

    resposta = TestClient(app).post(
        "/v1/admin/leads/importar",
        files=_csv_upload(csv_conteudo),
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["criados"] == 1
    assert corpo["sem_dados_essenciais"] == 1
    leads_criados = [obj for obj in session.adicionados if isinstance(obj, Lead)]
    assert len(leads_criados) == 1


def test_importar_leads_arquivo_vazio_retorna_400() -> None:
    _sessao_admin()

    resposta = TestClient(app).post(
        "/v1/admin/leads/importar",
        files={"arquivo": ("leads.csv", b"", "text/csv")},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 400


# --- Itens 41-43 da auditoria completa do CRM (06/09/2026): o dashboard já
# calculava volume por origem/vendedor e a foto atual do funil, mas nunca
# taxa de conversão -- só quem olhasse os números absolutos com calculadora
# na mão descobria se uma origem/vendedor/etapa é boa ou ruim de verdade. ---


@pytest.mark.asyncio
async def test_dashboard_calcula_taxa_conversao_por_vendedor() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[]),  # por_fase
            FakeResult(itens=[]),  # por_resultado
            FakeResult(itens=[]),  # motivos
            FakeResult(itens=[(5, 2, 0, 3, 1)]),  # prod: rid, abertas, atrasadas, ganhos, perdidos
            FakeResult(itens=[(5, "Ana")]),  # nomes
            FakeResult(itens=[]),  # por_origem
            FakeResult(itens=[]),  # por_origem_resultado
            FakeResult(itens=[]),  # leads
            FakeResult(itens=[]),  # entradas_proposta
            FakeResult(itens=[]),  # primeiro_contato
            FakeResult(itens=[]),  # propostas
            FakeResult(itens=[]),  # entradas_por_fase
        ]
    )

    resultado = await dashboard_funil_produtividade(session, usuario_teste())

    vendedor = resultado["produtividade"][0]
    assert vendedor["nome"] == "Ana"
    assert vendedor["ganhos"] == 3
    assert vendedor["perdidos"] == 1
    assert vendedor["taxa_conversao"] == 0.75


@pytest.mark.asyncio
async def test_dashboard_sem_fechamentos_devolve_taxa_zero_em_vez_de_erro() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[(5, 4, 0, 0, 0)]),  # nenhum ganho nem perda ainda
            FakeResult(itens=[(5, "Ana")]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
        ]
    )

    resultado = await dashboard_funil_produtividade(session, usuario_teste())

    assert resultado["produtividade"][0]["taxa_conversao"] == 0


@pytest.mark.asyncio
async def test_dashboard_calcula_conversao_por_origem() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[]),  # por_fase
            FakeResult(itens=[]),  # por_resultado
            FakeResult(itens=[]),  # motivos
            FakeResult(itens=[]),  # prod (vazio -- sem ids, pula a query de nomes)
            FakeResult(itens=[("site", 3), ("indicacao", 1)]),  # por_origem
            FakeResult(
                itens=[("site", "ganho", 2), ("site", "perdido", 1), ("indicacao", "ganho", 1)]
            ),  # por_origem_resultado
            FakeResult(itens=[]),  # leads
            FakeResult(itens=[]),  # entradas_proposta
            FakeResult(itens=[]),  # primeiro_contato
            FakeResult(itens=[]),  # propostas
            FakeResult(itens=[]),  # entradas_por_fase
        ]
    )

    resultado = await dashboard_funil_produtividade(session, usuario_teste())

    por_origem = {item["origem"]: item for item in resultado["conversao_por_origem"]}
    assert por_origem["site"]["total"] == 3
    assert por_origem["site"]["ganho"] == 2
    assert por_origem["site"]["perdido"] == 1
    assert por_origem["site"]["taxa_conversao"] == round(2 / 3, 4)
    assert por_origem["indicacao"]["taxa_conversao"] == 1.0


@pytest.mark.asyncio
async def test_dashboard_calcula_conversao_por_etapa_usando_historico() -> None:
    leads_fake = [
        SimpleNamespace(
            id=i,
            status=StatusLead.NOVO,
            proxima_acao_em=None,
            criado_em=None,
            atualizado_em=None,
            fase="contato_inicial",
        )
        for i in range(10)
    ]
    session = FakeSession(
        [
            FakeResult(itens=[]),  # por_fase
            FakeResult(itens=[]),  # por_resultado
            FakeResult(itens=[]),  # motivos
            FakeResult(itens=[]),  # prod
            FakeResult(itens=[]),  # por_origem
            FakeResult(itens=[]),  # por_origem_resultado
            FakeResult(itens=leads_fake),  # leads
            FakeResult(itens=[]),  # entradas_proposta
            FakeResult(itens=[]),  # primeiro_contato
            FakeResult(itens=[]),  # propostas
            FakeResult(
                itens=[
                    ("qualificado", 9),
                    ("relatorio_enviado", 7),
                    ("proposta_enviada", 5),
                    ("proposta_aceita", 3),
                    ("aguardando_pagamento", 3),
                    ("pagamento_confirmado", 2),
                    ("ganho", 2),
                    ("protocolo_inpi", 2),
                    ("processo_inpi", 1),
                ]
            ),  # entradas_por_fase
        ]
    )

    resultado = await dashboard_funil_produtividade(session, usuario_teste())

    por_fase = {item["fase"]: item for item in resultado["conversao_por_etapa"]}
    assert por_fase["contato_inicial"]["entradas"] == 10
    assert por_fase["contato_inicial"]["taxa_acumulada"] == 1.0
    assert por_fase["qualificado"]["entradas"] == 9
    assert por_fase["qualificado"]["taxa_da_etapa_anterior"] == round(9 / 10, 4)
    assert por_fase["proposta_enviada"]["taxa_acumulada"] == round(5 / 10, 4)
    assert por_fase["processo_inpi"]["entradas"] == 1
    assert por_fase["processo_inpi"]["taxa_da_etapa_anterior"] == round(1 / 2, 4)


# --- Itens 48-49 da auditoria completa do CRM (06/09/2026): não existia
# nenhuma soma do valor de propostas ainda em aberto (só receita já
# faturada), nem forecast ponderado pela chance histórica de fechamento --
# forecast_ponderado reaproveita a taxa_acumulada calculada nos itens
# 41-43 acima em vez de inventar um segundo modelo de probabilidade. ---


@pytest.mark.asyncio
async def test_dashboard_calcula_pipeline_previsto_e_forecast_ponderado() -> None:
    leads_fake = [
        SimpleNamespace(
            id=1,
            status=StatusLead.NOVO,
            proxima_acao_em=None,
            criado_em=None,
            atualizado_em=None,
            fase="proposta_enviada",
        ),
        SimpleNamespace(
            id=2,
            status=StatusLead.NOVO,
            proxima_acao_em=None,
            criado_em=None,
            atualizado_em=None,
            fase="qualificado",
        ),
    ]
    propostas_fake = [
        SimpleNamespace(
            lead_id=1,
            status="enviada",
            honorarios=Decimal("1000"),
            taxa_gru=Decimal("200"),
            pagamento_status="pendente",
            protocolo_em=None,
            aceito_em=None,
            enviado_em=None,
        ),
        SimpleNamespace(
            lead_id=2,
            status="visualizada",
            honorarios=Decimal("500"),
            taxa_gru=None,
            pagamento_status="pendente",
            protocolo_em=None,
            aceito_em=None,
            enviado_em=None,
        ),
        SimpleNamespace(
            lead_id=1,
            status="aceita",
            honorarios=Decimal("999"),
            taxa_gru=None,
            pagamento_status="pendente",
            protocolo_em=None,
            aceito_em=None,
            enviado_em=None,
        ),
    ]
    session = FakeSession(
        [
            FakeResult(itens=[]),  # por_fase
            FakeResult(itens=[]),  # por_resultado
            FakeResult(itens=[]),  # motivos
            FakeResult(itens=[]),  # prod
            FakeResult(itens=[]),  # por_origem
            FakeResult(itens=[]),  # por_origem_resultado
            FakeResult(itens=leads_fake),  # leads
            FakeResult(itens=[]),  # entradas_proposta
            FakeResult(itens=[]),  # primeiro_contato
            FakeResult(itens=propostas_fake),  # propostas
            FakeResult(itens=[("qualificado", 2), ("proposta_enviada", 1), ("ganho", 1)]),  # entradas_por_fase
        ]
    )

    resultado = await dashboard_funil_produtividade(session, usuario_teste())

    # Só as 2 propostas enviada/visualizada entram no pipeline -- a aceita
    # (999) já é receita em outro estágio, não "aberta" esperando decisão.
    assert resultado["pipeline_previsto"] == Decimal("1700")
    # lead 1 está em proposta_enviada (acumulada 1/2=0.5, igual à acumulada
    # de "ganho") -> probabilidade 1.0 -> 1200*1.0 = 1200.
    # lead 2 está em qualificado (acumulada 2/2=1.0) -> probabilidade
    # 0.5/1.0=0.5 -> 500*0.5 = 250.
    assert resultado["forecast_ponderado"] == Decimal("1450.00")


# --- Item 30 da auditoria completa do CRM (06/09/2026): endpoint dedicado de
# score de lead (ver app.crm.calcular_score_lead para a lógica). ---


def test_endpoint_score_lead_devolve_score_calculado() -> None:
    lead = Lead(
        id=7,
        organizacao_id=1,
        email="cliente@example.test",
        telefone="11999999999",
        criado_em=datetime.now(UTC) - timedelta(days=1),
    )
    _sessao_admin(
        FakeResult(scalar=lead),
        FakeResult(scalar=None),  # sem proposta perdida
        FakeResult(scalar=None),  # sem contato registrado
        FakeResult(scalar=None),  # sem resposta de e-mail
    )

    resposta = TestClient(app).get("/v1/admin/leads/7/score")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["fit_base"] == 40
    assert corpo["engajamento_bruto"] == 0
    assert corpo["score"] == 40


def test_endpoint_score_lead_404_quando_lead_nao_existe() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).get("/v1/admin/leads/999/score")

    assert resposta.status_code == 404


# --- "IA em sombra": endpoints de leitura e revisão humana da sugestão
# (a geração em si roda no worker -- ver tests/test_ia_sombra.py). ---


def test_endpoint_sugestao_ia_404_quando_nao_ha_sugestao_gerada() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).get("/v1/admin/leads/7/sugestao-ia")

    assert resposta.status_code == 404


def test_endpoint_sugestao_ia_devolve_a_mais_recente() -> None:
    sugestao = SugestaoIALead(
        id=3,
        organizacao_id=1,
        lead_id=7,
        modelo="qwen2.5:7b-instruct-q4_K_M",
        resumo="Lead qualificado, sem contato há 5 dias.",
        sugestao_proxima_acao="Ligar para retomar o atendimento.",
        status="pendente",
        baseado_em_evento_em=datetime.now(UTC),
    )
    _sessao_admin(FakeResult(scalar=sugestao))

    resposta = TestClient(app).get("/v1/admin/leads/7/sugestao-ia")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["resumo"] == "Lead qualificado, sem contato há 5 dias."
    assert corpo["status"] == "pendente"


def test_endpoint_revisar_sugestao_ia_registra_quem_revisou() -> None:
    sugestao = SugestaoIALead(
        id=3,
        organizacao_id=1,
        lead_id=7,
        modelo="qwen2.5:7b-instruct-q4_K_M",
        status="pendente",
        baseado_em_evento_em=datetime.now(UTC),
    )
    session = _sessao_admin(FakeResult(scalar=sugestao))

    resposta = TestClient(app).post(
        "/v1/admin/leads/7/sugestao-ia/3/revisar",
        json={"status": "aprovada"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    assert sugestao.status == "aprovada"
    assert sugestao.revisado_por is not None
    assert session.commits == 1


def test_endpoint_revisar_sugestao_ia_404_quando_nao_encontrada() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).post(
        "/v1/admin/leads/7/sugestao-ia/999/revisar",
        json={"status": "descartada"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 404


# --- "IA em sombra" (captação de leads): endpoints de leitura e revisão
# humana da qualificação gerada na chegada do lead. ---


def test_endpoint_qualificacao_ia_404_quando_nao_ha_qualificacao_gerada() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).get("/v1/admin/leads/7/qualificacao-ia")

    assert resposta.status_code == 404


def test_endpoint_qualificacao_ia_devolve_a_do_lead() -> None:
    registro = QualificacaoIALead(
        id=4,
        organizacao_id=1,
        lead_id=7,
        modelo="qwen2.5:7b-instruct-q4_K_M",
        prioridade="alta",
        observacao="Empresa grande, marca conhecida.",
        status="pendente",
    )
    _sessao_admin(FakeResult(scalar=registro))

    resposta = TestClient(app).get("/v1/admin/leads/7/qualificacao-ia")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["prioridade"] == "alta"
    assert corpo["observacao"] == "Empresa grande, marca conhecida."
    assert corpo["status"] == "pendente"


def test_endpoint_revisar_qualificacao_ia_registra_quem_revisou() -> None:
    registro = QualificacaoIALead(
        id=4, organizacao_id=1, lead_id=7, modelo="qwen2.5:7b-instruct-q4_K_M", prioridade="alta", status="pendente"
    )
    session = _sessao_admin(FakeResult(scalar=registro))

    resposta = TestClient(app).post(
        "/v1/admin/leads/7/qualificacao-ia/4/revisar",
        json={"status": "aprovada"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    assert registro.status == "aprovada"
    assert registro.revisado_por is not None
    assert session.commits == 1


def test_endpoint_revisar_qualificacao_ia_404_quando_nao_encontrada() -> None:
    _sessao_admin(FakeResult(scalar=None))

    resposta = TestClient(app).post(
        "/v1/admin/leads/7/qualificacao-ia/999/revisar",
        json={"status": "descartada"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 404


def test_listar_emails_leads_devolve_enderecos_do_filtro_atual() -> None:
    _sessao_admin(FakeResult(itens=["ana@example.com", "beto@example.com"]))

    resposta = TestClient(app).get("/v1/admin/leads-emails?status=novo")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["emails"] == ["ana@example.com", "beto@example.com"]
    assert corpo["total"] == 2


def test_listar_emails_leads_sem_permissao_pii_e_recusado() -> None:
    session = FakeSession([])
    usuario = usuario_teste(perfil="operador", permissoes=frozenset({"leads.export"}))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).get("/v1/admin/leads-emails")

    assert resposta.status_code == 403


def _lead_para_email(**overrides: object) -> Lead:
    base: dict = dict(
        id=9,
        organizacao_id=1,
        nome="Fulano de Tal",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        origem="processo",
        status=StatusLead.NOVO,
    )
    base.update(overrides)
    return Lead(**base)


def test_enviar_email_prospeccao_usa_modelo_configurado_e_registra_contato(monkeypatch) -> None:
    enviados = []

    async def _fake_enviar(destinatario: str, assunto: str, corpo: str, reply_to: str | None = None) -> None:
        enviados.append({"destinatario": destinatario, "assunto": assunto, "corpo": corpo, "reply_to": reply_to})

    monkeypatch.setattr("app.api.leads.enviar_email_prospeccao_lead", _fake_enviar)
    lead = _lead_para_email()
    org = SimpleNamespace(
        id=1, branding={"email_leads": {"assunto": "Oi {{lead.nome}}", "corpo": "Olá {{lead.nome}}!", "reply_to": ""}}
    )
    session = FakeSession([], objetos_get=[lead, org])
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/9/enviar-email-prospeccao", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 201
    assert enviados[0]["destinatario"] == "fulano@example.com"
    assert enviados[0]["assunto"] == "Oi Fulano de Tal"
    assert enviados[0]["corpo"] == "Olá Fulano de Tal!"
    corpo = resposta.json()
    assert corpo["canal"] == "email"
    assert session.adicionados[0].lead_id == 9


def test_enviar_email_prospeccao_sem_email_e_rejeitado() -> None:
    lead = _lead_para_email(email="")
    session = FakeSession([], objetos_get=[lead])
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/9/enviar-email-prospeccao", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 422


def test_enviar_email_prospeccao_propaga_falha_de_envio_como_502(monkeypatch) -> None:
    async def _fake_enviar(*args: object, **kwargs: object) -> None:
        raise RuntimeError("SMTP indisponivel")

    monkeypatch.setattr("app.api.leads.enviar_email_prospeccao_lead", _fake_enviar)
    lead = _lead_para_email()
    org = SimpleNamespace(id=1, branding={})
    session = FakeSession([], objetos_get=[lead, org])
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/9/enviar-email-prospeccao", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 502

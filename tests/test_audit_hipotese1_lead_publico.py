"""Testes de regressão da auditoria técnica (10/09/2026) — Hipótese 1:
comportamento de POST /v1/leads (upsert público de lead) em relação à
política de CRM (responsável, próxima ação e distribuição automática).

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from app.models import Lead, PoliticaCRM
from tests.conftest import FakeResult, FakeSession


def _payload(**overrides: object) -> dict[str, object]:
    base = {
        "nome": "Fulano de Tal",
        "email": "fulano.hipotese1@example.com",
        "telefone": "11999998888",
        "marca": "ACME HIPOTESE 1",
        "processo_numero": "123456789",
        "origem": "processo",
        "aceite_privacidade": True,
        "website": "",
    }
    base.update(overrides)
    return base


def test_lead_publico_sem_politica_configurada_fica_sem_responsavel_mas_com_proxima_acao() -> None:
    """Comportamento CONFIRMADO (código lido em app/api/leads.py::criar_lead +
    _garantir_proxima_acao_padrao + app/crm.py::aplicar_politica_oportunidade):

    Sem nenhuma PoliticaCRM cadastrada para a organização (caso mais comum),
    obter_politica_crm devolve um objeto default em memória com
    atribuir_ao_operador=False e distribuicao_automatica_ativa=False. Nenhum
    dos dois liga sozinho, então o lead público:
      - fica com responsavel_id=None (não há operador logado nesse fluxo);
      - NÃO fica sem próxima ação: _garantir_proxima_acao_padrao aplica um
        fallback fixo de 2 dias (DIAS_PROXIMA_ACAO_CAPTACAO_PADRAO) quando a
        política também não define dias_proxima_acao_padrao.
    """
    # 1ª query: consulta_existente (nenhum lead com esse e-mail/telefone).
    # 2ª query: obter_politica_crm (nenhuma política cadastrada -> None).
    sessao = FakeSession([FakeResult(scalar=None), FakeResult(scalar=None)])

    async def _override() -> object:
        yield sessao

    app.dependency_overrides[get_session] = _override
    antes = datetime.now(UTC)

    resposta = TestClient(app).post("/v1/leads", json=_payload())

    assert resposta.status_code == 201
    # Achado crítico da Fase 12 (22/09/2026): a resposta pública de POST
    # /v1/leads não expõe mais responsavel_id/proxima_acao_em (dados
    # internos de CRM) -- este teste passa a inspecionar o Lead gravado na
    # FakeSession em vez do corpo da resposta.
    lead = next(obj for obj in sessao.adicionados if isinstance(obj, Lead))
    assert lead.responsavel_id is None, "Lead público deveria ficar sem responsável (achado confirmado)"
    assert lead.proxima_acao_em is not None, "Lead público NÃO deveria ficar sem próxima ação (fallback de 2 dias)"
    depois = datetime.now(UTC)
    assert antes + timedelta(days=2) <= lead.proxima_acao_em <= depois + timedelta(days=2, minutes=1)


def test_lead_publico_nao_aplica_atribuir_ao_operador_mesmo_com_politica_ligada() -> None:
    """Comportamento CONFIRMADO: aplicar_politica_oportunidade só atribui o
    lead ao "operador logado" quando recebe operador_id. Em
    _garantir_proxima_acao_padrao (chamada por criar_lead), a função é
    invocada SEM operador_id (não existe operador logado num POST público) --
    então mesmo com atribuir_ao_operador=True na política, o lead público
    continua sem responsável. A política "atribuir ao operador" é, na
    prática, inerte para o formulário público; ela só faz efeito em fluxos
    onde um operador autenticado está registrando o lead (ex.: Consulta de
    marcas, app/api/consulta.py).
    """
    politica = PoliticaCRM(
        id=1,
        organizacao_id=1,
        exigir_responsavel=True,
        atribuir_ao_operador=True,
        exigir_proxima_acao=True,
        dias_proxima_acao_padrao=None,
        distribuicao_automatica_ativa=False,
        ultimo_responsavel_distribuido_id=None,
        horas_sla_primeiro_atendimento=None,
    )
    sessao = FakeSession([FakeResult(scalar=None), FakeResult(scalar=politica)])

    async def _override() -> object:
        yield sessao

    app.dependency_overrides[get_session] = _override

    resposta = TestClient(app).post("/v1/leads", json=_payload(email="hipotese1.atribuir@example.com"))

    assert resposta.status_code == 201
    lead = next(obj for obj in sessao.adicionados if isinstance(obj, Lead))
    assert lead.responsavel_id is None


def test_lead_publico_com_distribuicao_automatica_ativa_recebe_responsavel() -> None:
    """Contraponto: distribuicao_automatica_ativa NÃO depende de operador_id
    (distribuir_lead_automaticamente só olha lead.responsavel_id e a própria
    política), então é a única alavanca da política que atribui responsável
    a um lead criado pelo formulário público. Confirma que a "regra mais
    segura" para responsabilizar leads públicos, dentro do desenho atual, é
    ligar distribuicao_automatica_ativa (ou rodar o rodízio em lote depois),
    não atribuir_ao_operador.
    """
    politica = PoliticaCRM(
        id=1,
        organizacao_id=1,
        exigir_responsavel=True,
        atribuir_ao_operador=False,
        exigir_proxima_acao=True,
        dias_proxima_acao_padrao=None,
        distribuicao_automatica_ativa=True,
        ultimo_responsavel_distribuido_id=None,
        horas_sla_primeiro_atendimento=None,
    )
    sessao = FakeSession(
        [
            FakeResult(scalar=None),
            FakeResult(scalar=politica),
            FakeResult(itens=[9]),
            FakeResult(itens=[]),  # achado do usuário (16/09/2026): carga atual por responsavel
        ]
    )

    async def _override() -> object:
        yield sessao

    app.dependency_overrides[get_session] = _override

    resposta = TestClient(app).post("/v1/leads", json=_payload(email="hipotese1.distribuicao@example.com"))

    assert resposta.status_code == 201
    lead = next(obj for obj in sessao.adicionados if isinstance(obj, Lead))
    assert lead.responsavel_id == 9
    assert politica.ultimo_responsavel_distribuido_id == 9

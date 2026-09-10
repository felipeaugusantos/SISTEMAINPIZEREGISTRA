"""Testes de regressão da auditoria técnica (10/09/2026) — Hipótese 3:
o status "descartado" não parece ter fase/coluna própria no Kanban.

Correção P1 aplicada em 10/09/2026: nova coluna "perdidos" em KANBAN_ETAPAS
(app/api/leads.py); _kanban_etapa() agora devolve "perdidos" para qualquer
lead com status==DESCARTADO, independentemente da fase em que estava
congelado. mover_lead_kanban (arrastar o card) recusa a etapa "perdidos"
explicitamente -- descartar exige motivo_perda pelo PATCH de status
(achado H4), não um simples arrasto de card.

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Lead, StatusLead
from tests.conftest import FakeResult, auth_override, sessao_override, usuario_teste


def _lead(**overrides: object) -> Lead:
    base = dict(
        organizacao_id=1,
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        origem="processo",
        aceite_marketing=False,
    )
    base.update(overrides)
    lead = Lead(**base)
    # Construção direta (sem flush real) não aplica server_default -- sem
    # isso, listar_leads_kanban quebra com TypeError ao calcular o SLA
    # (agora - None). Não é o achado desta hipótese; é só o setup do teste.
    lead.atualizado_em = datetime.now(UTC)
    return lead


def test_lead_descartado_ainda_estagio_qualificado_vai_para_coluna_perdidos() -> None:
    """CORRIGIDO (achado H3/P1, 10/09/2026): _kanban_etapa() agora checa
    status==DESCARTADO antes de olhar a fase -- lead.fase continua
    congelado no que era antes do descarte (MAPA_STATUS_FASE não tem
    entrada para "descartado"), mas isso não importa mais para a coluna:
    o card vai direto para "perdidos", independentemente de onde estava."""
    lead_descartado = _lead(
        id=1,
        status=StatusLead.DESCARTADO,
        fase="qualificado",
        resultado="perdido",
        motivo_perda="sem_resposta",
    )
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[lead_descartado]))
    usuario = usuario_teste()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).get("/v1/admin/leads-kanban")

    assert resposta.status_code == 200
    corpo = resposta.json()
    cartao = corpo["cards"][0]
    assert cartao["status"] == "descartado"
    assert cartao["etapa"] == "perdidos"
    assert any(etapa["id"] == "perdidos" for etapa in corpo["etapas"]), "coluna 'Perdidos' deve existir no board"


def test_lead_descartado_nao_e_mais_contado_junto_com_a_coluna_qualificado() -> None:
    """CORRIGIDO: um lead descartado que estava em "qualificado" antes do
    descarte não conta mais na coluna "Qualificado" junto com oportunidades
    genuinamente abertas -- só o lead aberto aparece lá; o descartado vai
    para "perdidos"."""
    lead_aberto = _lead(
        id=1, status=StatusLead.QUALIFICADO, fase="qualificado", responsavel_id=5, proxima_acao_em=datetime.now(UTC)
    )
    lead_descartado = _lead(id=2, status=StatusLead.DESCARTADO, fase="qualificado", resultado="perdido")
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[lead_aberto, lead_descartado]))
    usuario = usuario_teste()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).get("/v1/admin/leads-kanban")

    assert resposta.status_code == 200
    cartoes = resposta.json()["cards"]
    cartoes_qualificado = [c for c in cartoes if c["etapa"] == "qualificado"]
    cartoes_perdidos = [c for c in cartoes if c["etapa"] == "perdidos"]
    assert len(cartoes_qualificado) == 1
    assert cartoes_qualificado[0]["status"] == "qualificado"
    assert len(cartoes_perdidos) == 1
    assert cartoes_perdidos[0]["status"] == "descartado"


def test_mover_kanban_recusa_a_etapa_perdidos() -> None:
    """CORRIGIDO: arrastar um card diretamente para a coluna "Perdidos" é
    recusado (422) -- descartar exige motivo_perda pelo PATCH de status
    (achado H4/P1), não um simples arrasto no board."""
    app.dependency_overrides[get_session] = sessao_override()
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/1/kanban",
        json={"etapa": "perdidos"},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 422


def test_lead_convertido_vai_para_coluna_ganho_diferente_de_descartado() -> None:
    """CONTRASTE (comportamento CORRETO, não é o mesmo problema):
    "convertido" TEM entrada em MAPA_STATUS_FASE (-> "ganho"), e existe uma
    etapa "ganho" própria em KANBAN_ETAPAS. Um lead convertido é
    corretamente separado das oportunidades abertas numa coluna terminal
    distinta -- a assimetria é específica de "descartado", que não tem
    nenhuma fase/coluna terminal equivalente."""
    lead_convertido = _lead(id=3, status=StatusLead.CONVERTIDO, fase="ganho", resultado="ganho")
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[lead_convertido]))
    usuario = usuario_teste()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).get("/v1/admin/leads-kanban")

    assert resposta.status_code == 200
    cartao = resposta.json()["cards"][0]
    assert cartao["etapa"] == "ganho"

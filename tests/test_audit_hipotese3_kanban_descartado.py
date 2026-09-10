"""Testes de regressão da auditoria técnica (10/09/2026) — Hipótese 3:
o status "descartado" não parece ter fase/coluna própria no Kanban.

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.auth import obter_usuario_atual
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


def test_lead_descartado_ainda_estagio_qualificado_continua_na_coluna_qualificado() -> None:
    """CONFIRMADO: MAPA_STATUS_FASE (app/crm.py) não tem entrada para
    "descartado" -- sincronizar_fase_por_status() (chamada em
    atualizar_status_lead ao mudar o status) devolve False e NÃO mexe em
    lead.fase quando o status vira descartado. lead.fase continua sendo o
    que era ANTES do descarte (aqui, "qualificado"); _kanban_etapa deriva a
    coluna só da fase (fora dos dois casos especiais de contato_inicial/
    relatorio_enviado), então o card permanece na coluna "Qualificado" --
    misturado com oportunidades ainda abertas -- em vez de ir para uma
    coluna própria de "Perdidos"."""
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
    cartao = resposta.json()["cards"][0]
    assert cartao["status"] == "descartado"
    assert cartao["etapa"] == "qualificado", (
        "Achado: lead descartado não vai para nenhuma coluna própria de 'Perdidos' -- "
        "permanece na última coluna ativa em que estava."
    )


def test_lead_descartado_e_contado_no_kanban_junto_com_oportunidades_abertas() -> None:
    """CONFIRMADO: listar_leads_kanban (GET /v1/admin/leads-kanban) filtra
    só organizacao_id e arquivado_em.is_(None) -- NÃO exclui
    status in (CONVERTIDO, DESCARTADO), ao contrário do dashboard principal
    (dashboard_funil_produtividade usa
    Lead.status.not_in((CONVERTIDO, DESCARTADO)) para "aberta"). Um lead
    descartado aparece nos `cards` e conta no total da sua coluna, junto
    com oportunidades genuinamente abertas."""
    lead_aberto = _lead(
        id=1, status=StatusLead.QUALIFICADO, fase="qualificado", responsavel_id=5, proxima_acao_em=datetime.now(UTC)
    )
    lead_descartado = _lead(id=2, status=StatusLead.DESCARTADO, fase="qualificado", resultado="perdido")
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[lead_aberto, lead_descartado]))
    usuario = usuario_teste()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).get("/v1/admin/leads-kanban")

    assert resposta.status_code == 200
    cartoes_qualificado = [c for c in resposta.json()["cards"] if c["etapa"] == "qualificado"]
    assert len(cartoes_qualificado) == 2, "O lead descartado é contado junto com o aberto na mesma coluna"
    status_presentes = {c["status"] for c in cartoes_qualificado}
    assert status_presentes == {"qualificado", "descartado"}


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

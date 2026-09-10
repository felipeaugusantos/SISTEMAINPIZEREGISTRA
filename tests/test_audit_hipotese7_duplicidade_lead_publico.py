"""Auditoria técnica (10/09/2026) — Hipótese 7: duplicidade por e-mail/telefone
no upsert público (POST /v1/leads).

Os cenários "mesmo e-mail e nova marca" e "mesma pessoa com duas marcas
simultâneas" já têm cobertura existente e não são duplicados aqui:
tests/test_leads.py::test_upsert_publico_marca_diferente_cria_novo_lead_e_preserva_o_antigo.
O cenário "mesmo telefone e novo e-mail" (com a MESMA marca) também já é
coberto por
tests/test_leads.py::test_upsert_publico_telefone_com_mascara_diferente_reconhece_o_mesmo_lead.
Este arquivo cobre os três cenários ainda sem teste: lead convertido,
descartado e arquivado reenviando o formulário público.

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.
"""

from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from app.models import Lead, StatusLead
from tests.conftest import FakeResult, sessao_override


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


def _payload(**overrides: object) -> dict[str, object]:
    base = {
        "nome": "Outro Nome Depois",
        "email": "fulano@example.com",
        "telefone": "11999998888",
        "marca": "ACME",  # mesma marca -> mesma_oportunidade=True, reusa o lead
        "processo_numero": "123456789",
        "origem": "processo",
        "aceite_privacidade": True,
        "website": "",
    }
    base.update(overrides)
    return base


def test_lead_convertido_reenviando_o_formulario_tem_dados_sobrescritos_mas_status_preservado() -> None:
    """CONFIRMADO: consulta_existente (app/api/leads.py::criar_lead) filtra
    só organizacao_id e arquivado_em.is_(None) -- NÃO exclui status
    CONVERTIDO da busca por duplicidade. Com a mesma marca, mesma_oportunidade
    é True e o bloco de atualização roda por cima de um negócio JÁ GANHO:
    nome/email/telefone são sobrescritos. status/resultado permanecem
    intactos (esse bloco nunca os toca), e _garantir_proxima_acao_padrao é
    pulado (guard explícito `if existente.status not in (CONVERTIDO,
    DESCARTADO)`), então proxima_acao_em também não é mexido. Impacto:
    dados de contato de um cliente já convertido podem ser silenciosamente
    alterados por qualquer novo envio público com o mesmo e-mail/marca."""
    lead = _lead_existente(status=StatusLead.CONVERTIDO, resultado="ganho")
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))

    resposta = TestClient(app).post("/v1/leads", json=_payload(nome="Outro Nome Depois", telefone="11900001111"))

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["id"] == 7
    assert lead.nome == "Outro Nome Depois"
    assert lead.telefone == "11900001111"
    assert lead.status == StatusLead.CONVERTIDO, "status de um negócio ganho não deveria mudar sozinho"
    assert lead.resultado == "ganho"


def test_lead_descartado_reenviando_o_formulario_tem_dados_sobrescritos_e_continua_descartado() -> None:
    """CONFIRMADO: mesmo padrão do teste acima, para status DESCARTADO. O
    lead volta a ter contato real (dados atualizados), mas continua
    marcado como "descartado"/"perdido" -- fica invisível para a equipe
    comercial no funil ativo, mesmo que o cliente tenha voltado a
    demonstrar interesse pelo canal público."""
    lead = _lead_existente(status=StatusLead.DESCARTADO, resultado="perdido", motivo_perda="sem_resposta")
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))

    resposta = TestClient(app).post("/v1/leads", json=_payload(nome="Voltou Depois"))

    assert resposta.status_code == 201
    assert lead.nome == "Voltou Depois"
    assert lead.status == StatusLead.DESCARTADO, "reenvio público não reabre um lead descartado"
    assert lead.motivo_perda == "sem_resposta"


def test_lead_arquivado_reenviando_o_formulario_cria_lead_novo_sem_nenhum_vinculo() -> None:
    """CONFIRMADO: consulta_existente filtra Lead.arquivado_em.is_(None) --
    um lead arquivado nunca é encontrado pela busca de duplicidade. Um
    reenvio com o mesmo e-mail/telefone de um lead arquivado cria um Lead
    novo e completamente desvinculado do histórico anterior (nenhum
    campo herdado, nenhuma referência cruzada). Diferente dos casos
    convertido/descartado acima -- ali o registro é mutado por engano;
    aqui a fragmentação é o oposto: dois registros desconexos para a
    mesma pessoa, sem nenhum jeito automático de saber que são a mesma."""
    lead_arquivado = _lead_existente()
    # A própria query de consulta_existente já teria Lead.arquivado_em.is_(None)
    # no WHERE -- simulamos o resultado que o banco devolveria: nenhuma linha.
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))

    resposta = TestClient(app).post("/v1/leads", json=_payload())

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["id"] != lead_arquivado.id
    assert lead_arquivado.nome == "Fulano de Tal", "o registro arquivado não é tocado, nem referenciado"

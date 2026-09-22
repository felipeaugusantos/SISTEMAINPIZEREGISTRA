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

Correção P1 aplicada em 10/09/2026: convertido não tem mais os dados
sobrescritos (só registra o reenvio, sem mutar o negócio já ganho);
descartado é reaberto (status volta a "novo") em vez de continuar
descartado com os dados trocados por baixo. Arquivado permanece sem
mudança (decisão registrada no relatório de auditoria).

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.
"""

from datetime import UTC, datetime

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
        # LeadPublicoResponse (Fase 12) exige criado_em -- em produção o
        # servidor sempre preenche via server_default, mas o objeto Lead
        # construído a mão nos testes precisa do valor explícito.
        "criado_em": datetime.now(UTC),
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


def test_lead_convertido_reenviando_o_formulario_nao_tem_dados_sobrescritos() -> None:
    """CORRIGIDO (achado H7/P1, 10/09/2026): consulta_existente (app/api/
    leads.py::criar_lead) continua encontrando o lead CONVERTIDO (não
    exclui esse status da busca por duplicidade), mas o bloco de
    "mesma_oportunidade" agora detecta status==CONVERTIDO antes de mutar
    qualquer campo e devolve o registro tal como estava -- nome/email/
    telefone do negócio já ganho não são mais sobrescritos por um reenvio
    público."""
    lead = _lead_existente(status=StatusLead.CONVERTIDO, resultado="ganho")
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead))

    resposta = TestClient(app).post("/v1/leads", json=_payload(nome="Outro Nome Depois", telefone="11900001111"))

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["id"] == 7
    assert lead.nome == "Fulano de Tal", "dados do negócio já ganho não devem ser sobrescritos"
    assert lead.telefone == "11999998888"
    assert lead.status == StatusLead.CONVERTIDO
    assert lead.resultado == "ganho"


def test_lead_descartado_reenviando_o_formulario_e_reaberto() -> None:
    """CORRIGIDO (achado H7/P1, 10/09/2026): um lead DESCARTADO que reenvia o
    formulário público agora é reaberto -- status volta a "novo",
    resultado/motivo_perda são limpos e o lead volta a receber
    proxima_acao_em (mesmo fallback usado para lead novo), em vez de
    continuar invisível para a equipe comercial com os dados trocados por
    baixo."""
    lead = _lead_existente(status=StatusLead.DESCARTADO, resultado="perdido", motivo_perda="sem_resposta")
    # 1ª query: consulta_existente. 2ª: obter_politica_crm (dentro de
    # _garantir_proxima_acao_padrao, agora executada pois o lead deixou de
    # estar em CONVERTIDO/DESCARTADO).
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lead), FakeResult(scalar=None))

    resposta = TestClient(app).post("/v1/leads", json=_payload(nome="Voltou Depois"))

    assert resposta.status_code == 201
    assert lead.nome == "Voltou Depois"
    assert lead.status == StatusLead.NOVO, "reenvio público reabre um lead descartado"
    assert lead.resultado is None
    assert lead.motivo_perda is None
    assert lead.proxima_acao_em is not None


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

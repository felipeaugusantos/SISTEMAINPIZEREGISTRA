from datetime import UTC, datetime

from app.api.leads import (
    _consulta_propostas_dashboard,
    _tempo_medio_ate_proposta_dias,
    _tempo_medio_primeiro_atendimento_horas,
)
from app.models import Lead, StatusLead


def _lead(**kwargs: object) -> Lead:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "nome": "Cliente Teste",
        "email": "cliente@teste.local",
        "telefone": "11999999999",
        "marca": "ACME",
        "fase": "proposta_enviada",
        "criado_em": datetime(2026, 1, 1, tzinfo=UTC),
        "atualizado_em": datetime(2026, 1, 10, tzinfo=UTC),
    }
    base.update(kwargs)
    return Lead(**base)


# --- Achado L10 do plano Leads/CRM (03/09/2026): tempo até proposta usava atualizado_em ---


def test_tempo_ate_proposta_usa_historico_de_fase_quando_disponivel() -> None:
    lead = _lead(id=1)
    entradas = {1: datetime(2026, 1, 4, tzinfo=UTC)}  # entrou em proposta_enviada 3 dias após criado_em
    valores = _tempo_medio_ate_proposta_dias([lead], entradas)
    assert valores == [3.0]


def test_tempo_ate_proposta_cai_para_atualizado_em_sem_historico() -> None:
    lead = _lead(id=2)  # atualizado_em = 9 dias após criado_em
    valores = _tempo_medio_ate_proposta_dias([lead], {})
    assert valores == [9.0]


def test_tempo_ate_proposta_ignora_lead_em_fase_anterior() -> None:
    lead = _lead(id=3, fase="contato_inicial")
    valores = _tempo_medio_ate_proposta_dias([lead], {3: datetime(2026, 1, 2, tzinfo=UTC)})
    assert valores == []


def test_tempo_ate_proposta_considera_todas_as_fases_pos_proposta() -> None:
    entradas = {10: datetime(2026, 1, 3, tzinfo=UTC)}
    for fase in ("proposta_enviada", "proposta_aceita", "pagamento_realizado", "protocolo_inpi", "processo_inpi"):
        lead = _lead(id=10, fase=fase)
        assert _tempo_medio_ate_proposta_dias([lead], entradas) == [2.0]


# --- Achado L11 do plano Leads/CRM (03/09/2026): taxa_pagamento/protocolo com universo inconsistente ---


def test_consulta_propostas_dashboard_exclui_rascunho_e_lead_arquivado() -> None:
    sql = str(_consulta_propostas_dashboard(1).compile(compile_kwargs={"literal_binds": True}))
    assert "rascunho" in sql
    assert "arquivado_em" in sql


# --- Achado P1 da auditoria de Leads (03/09/2026): não existia SLA de
# primeiro atendimento -- só tempo até a proposta, uma etapa bem mais adiante. ---


def test_primeiro_atendimento_usa_o_contato_mais_antigo() -> None:
    lead = _lead(id=1, status=StatusLead.EM_CONTATO)
    primeiro_contato = {1: datetime(2026, 1, 1, 6, 0, tzinfo=UTC)}  # 6h após criado_em (00:00)
    valores = _tempo_medio_primeiro_atendimento_horas([lead], primeiro_contato)
    assert valores == [6.0]


def test_primeiro_atendimento_ignora_lead_sem_contato_registrado() -> None:
    lead = _lead(id=2, status=StatusLead.NOVO)
    valores = _tempo_medio_primeiro_atendimento_horas([lead], {})
    assert valores == []

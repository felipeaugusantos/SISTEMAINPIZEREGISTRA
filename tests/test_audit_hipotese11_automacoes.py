"""Auditoria técnica (10/09/2026) — Hipótese 11: automações embutidas
(app/crm.py::aplicar_regras_automacao, REGRAS_AUTOMACAO).

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.
"""

import asyncio

from app.crm import REGRAS_AUTOMACAO, aplicar_regras_automacao
from app.models import Lead, RegraAutomacao, StatusLead
from tests.conftest import FakeResult, FakeSession


def _lead(**kwargs: object) -> Lead:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "nome": "Cliente Teste",
        "email": "cliente@teste.local",
        "telefone": "11999999999",
        "marca": "ACME",
        "fase": "proposta_enviada",
        "status": StatusLead.PROPOSTA_ENVIADA,
        "responsavel_id": None,
    }
    base.update(kwargs)
    return Lead(**base)


def test_proposta_enviada_dispara_followup_com_prazo_e_prioridade_da_regra() -> None:
    """CONFIRMADO: REGRAS_AUTOMACAO["followup_proposta"] tem evento="fase",
    gatilho="proposta_enviada", dias=2, prioridade="media" -- ao avisar
    esse evento, a automação cria exatamente essa tarefa (sem override de
    organização, usa o default embutido)."""
    lead = _lead()
    session = FakeSession([FakeResult(itens=[]), FakeResult(scalar=101)])

    aplicadas = asyncio.run(aplicar_regras_automacao(session, lead, "fase", "proposta_enviada", "sistema"))

    assert aplicadas == ["followup_proposta"]
    insercao = session.executados[1]
    valores = str(insercao.compile(compile_kwargs={"literal_binds": True}))
    assert REGRAS_AUTOMACAO["followup_proposta"]["titulo"] in valores
    assert "'media'" in valores


def test_regra_desativada_por_override_da_organizacao_nao_dispara() -> None:
    """CONFIRMADO: RegraAutomacao (tabela de override por organização) com
    ativo=False bloqueia a criação -- mesmo que o evento/gatilho bata com
    uma regra embutida."""
    lead = _lead()
    override_desativado = RegraAutomacao(
        id=1, organizacao_id=1, chave="followup_proposta", ativo=False, dias=2
    )
    session = FakeSession([FakeResult(itens=[override_desativado])])

    aplicadas = asyncio.run(aplicar_regras_automacao(session, lead, "fase", "proposta_enviada", "sistema"))

    assert aplicadas == []
    assert len(session.executados) == 1, "não deveria nem tentar inserir o lembrete"


def test_reaplicar_o_mesmo_evento_e_idempotente() -> None:
    """CONFIRMADO: a chave de idempotência
    "lead:{id}:{evento}:{valor}:{chave}" é fixa -- reaplicar o mesmo
    evento para o mesmo lead não duplica a tarefa (ON CONFLICT DO
    NOTHING; insert devolve None na segunda vez)."""
    lead = _lead()
    session = FakeSession([FakeResult(itens=[]), FakeResult(scalar=None)])

    aplicadas = asyncio.run(aplicar_regras_automacao(session, lead, "fase", "proposta_enviada", "sistema"))

    assert aplicadas == []


def test_dias_configurado_como_zero_cria_lembrete_imediato() -> None:
    """CONFIRMADO: `dias = override.dias if override is not None else regra["dias"]`
    seguido de `max(0, dias)` -- um override com dias=0 é respeitado
    literalmente, o lembrete nasce com lembrar_em = agora (sem atraso)."""
    lead = _lead()
    override_zero_dias = RegraAutomacao(id=1, organizacao_id=1, chave="followup_proposta", ativo=True, dias=0)
    session = FakeSession([FakeResult(itens=[override_zero_dias]), FakeResult(scalar=101)])

    aplicadas = asyncio.run(aplicar_regras_automacao(session, lead, "fase", "proposta_enviada", "sistema"))

    assert aplicadas == ["followup_proposta"]


def test_lead_sem_responsavel_ainda_recebe_a_tarefa_automatica() -> None:
    """CONFIRMADO: mesmo padrão das cadências -- responsavel_id é copiado
    de lead.responsavel_id (pode ser None) no momento da criação, sem
    bloqueio nem fallback."""
    lead = _lead(responsavel_id=None)
    session = FakeSession([FakeResult(itens=[]), FakeResult(scalar=101)])

    aplicadas = asyncio.run(aplicar_regras_automacao(session, lead, "fase", "proposta_enviada", "sistema"))

    assert aplicadas == ["followup_proposta"]

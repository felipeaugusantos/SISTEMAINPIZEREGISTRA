"""Achado do usuário (23/09/2026): a tela de lembretes do CRM mostrava
1102 "vencidos" -- quase todos vindos da automação de reengajamento por
inatividade (app/worker.py, job crm.reengajamento_inatividade), que
criava um lembrete novo por lead a cada semana ISO sem nunca cancelar o
da semana anterior. Só 310 leads estavam de fato parados; o resto era
lembrete velho acumulado (1101 lembretes para 310 leads, média de 3,5
por lead).

Corrigido em app/crm.py::gerar_lembretes_reengajamento_inatividade
(extraída de app/worker.py, mesmo padrão de
gerar_lembretes_sla_primeiro_atendimento): ao criar o lembrete da
semana atual, cancela qualquer lembrete "pendente" anterior da mesma
automação para o mesmo lead -- só um fica ativo por vez.

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real.
"""

import asyncio

from app.crm import gerar_lembretes_reengajamento_inatividade
from app.models import Lead, StatusLead
from tests.conftest import FakeResult, FakeSession


def _lead_parado(**overrides: object) -> Lead:
    base = dict(
        id=63,
        organizacao_id=1,
        nome="Cliente Parado",
        email="parado@example.test",
        telefone="11999999999",
        marca="Marca X",
        status=StatusLead.EM_CONTATO,
        fase="contato_inicial",
        responsavel_id=6,
        proxima_acao_em=None,
    )
    base.update(overrides)
    return Lead(**base)


def test_reengajamento_cria_lembrete_para_lead_parado_e_agrupa_por_responsavel() -> None:
    lead = _lead_parado()
    session = FakeSession(
        [
            FakeResult(itens=[lead]),  # select leads parados
            FakeResult(scalar=99),  # insert do lembrete desta semana (novo)
            FakeResult(),  # cancelamento de lembretes de semanas anteriores
        ]
    )

    criados, atrasados = asyncio.run(gerar_lembretes_reengajamento_inatividade(session))

    assert criados == 1
    assert atrasados == {6: [lead]}
    assert len(session.executados) == 3


def test_reengajamento_nao_conta_quando_ja_existe_lembrete_da_semana() -> None:
    # ON CONFLICT DO NOTHING devolve None quando o lembrete desta semana ISO
    # já existe (job rodando de hora em hora) -- não deve contar como
    # "criado" de novo, mas o cancelamento de lembretes velhos ainda roda.
    lead = _lead_parado()
    session = FakeSession(
        [
            FakeResult(itens=[lead]),
            FakeResult(scalar=None),
            FakeResult(),
        ]
    )

    criados, atrasados = asyncio.run(gerar_lembretes_reengajamento_inatividade(session))

    assert criados == 0
    assert atrasados == {}
    assert len(session.executados) == 3


def test_reengajamento_sempre_cancela_lembretes_de_semanas_anteriores() -> None:
    """O achado do usuário: mesmo em semanas onde o lembrete atual já existe
    (job repetido), o cancelamento das semanas anteriores precisa rodar --
    é ele que impede o acúmulo, não a criação em si."""
    lead_a = _lead_parado(id=63)
    lead_b = _lead_parado(id=65, responsavel_id=None)
    session = FakeSession(
        [
            FakeResult(itens=[lead_a, lead_b]),
            FakeResult(scalar=1),  # insert lead_a
            FakeResult(),  # cancela semanas anteriores do lead_a
            FakeResult(scalar=2),  # insert lead_b
            FakeResult(),  # cancela semanas anteriores do lead_b
        ]
    )

    criados, atrasados = asyncio.run(gerar_lembretes_reengajamento_inatividade(session))

    assert criados == 2
    # lead_b sem responsavel_id não entra no agrupamento de notificação.
    assert atrasados == {6: [lead_a]}
    assert len(session.executados) == 5

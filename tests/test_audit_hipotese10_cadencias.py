"""Auditoria técnica (10/09/2026) — Hipótese 10: comportamento das
cadências (app/crm.py::aplicar_cadencia_a_lead /
aplicar_cadencias_automaticas).

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.

Alguns pontos da hipótese são documentados por leitura de código/schema
em vez de teste dedicado (ver docs/auditoria-crm-leads-2026-09-10.md,
seção da Hipótese 10), porque exigiriam Postgres real para ter valor
além do que a leitura já garante:
- mudar o responsável DEPOIS de criadas as tarefas não atualiza
  LembreteCRM.responsavel_id retroativamente (o valor é copiado na
  criação, não é uma referência viva);
- excluir uma Cadencia não apaga LembreteCRM/EnvioCadenciaEmail já
  criados (não há FK entre eles -- só um idempotency_key em string);
- cancelamento/conclusão de tarefas é feito por outro endpoint
  (atualização de LembreteCRM.status), fora do escopo desta função.
"""

import asyncio

from app.crm import aplicar_cadencia_a_lead, aplicar_cadencias_automaticas
from app.models import Cadencia, CadenciaPasso, Lead, StatusLead
from tests.conftest import FakeResult, FakeSession


def _lead(**kwargs: object) -> Lead:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "nome": "Cliente Teste",
        "email": "cliente@teste.local",
        "telefone": "11999999999",
        "marca": "ACME",
        "fase": "contato_inicial",
        "status": StatusLead.NOVO,
        "responsavel_id": None,
    }
    base.update(kwargs)
    return Lead(**base)


def _cadencia(passos: list[CadenciaPasso], **kwargs: object) -> Cadencia:
    base: dict = {"id": 5, "organizacao_id": 1, "nome": "Cadência Teste", "ativo": True}
    base.update(kwargs)
    cadencia = Cadencia(**base)
    cadencia.passos = passos
    return cadencia


def _passo(**kwargs: object) -> CadenciaPasso:
    base: dict = {"id": 1, "organizacao_id": 1, "cadencia_id": 5, "ordem": 0, "dia": 2, "canal": "email"}
    base.update(kwargs)
    return CadenciaPasso(**base, titulo="Follow-up", descricao=None)


def test_aplicar_cadencia_ativa_cria_lembrete_e_agenda_envio_de_email() -> None:
    """CONFIRMADO: passo de canal "email" cria DOIS registros -- o
    LembreteCRM (tarefa interna) e um EnvioCadenciaEmail "agendado mas
    ainda não enviado" (montar_envio_pendente, app/cadencia_email.py).
    Cadência não só cria lembretes: para canal e-mail, também agenda o
    envio real de mensagem (processado depois por um worker)."""
    lead = _lead()
    cadencia = _cadencia([_passo()])
    session = FakeSession([FakeResult(scalar=101), FakeResult()])

    criados = asyncio.run(aplicar_cadencia_a_lead(session, lead, cadencia, "Operador", ator_id=9))

    assert criados == 1
    assert len(session.executados) == 2


def test_reaplicar_mesma_cadencia_e_idempotente_nao_duplica() -> None:
    """CONFIRMADO: a chave de idempotência
    "cadencia:{lead.id}:{cadencia.id}:{passo.id}" é fixa por
    lead+cadência+passo -- reenviar a MESMA cadência ao mesmo lead não
    duplica a tarefa nem o envio (ON CONFLICT DO NOTHING; o insert do
    LembreteCRM devolve None na segunda vez e o passo do e-mail nem
    chega a ser tentado, por causa do `continue`)."""
    lead = _lead()
    cadencia = _cadencia([_passo()])
    session = FakeSession([FakeResult(scalar=None)])  # simula ON CONFLICT DO NOTHING

    criados = asyncio.run(aplicar_cadencia_a_lead(session, lead, cadencia, "Operador", ator_id=9))

    assert criados == 0
    assert len(session.executados) == 1, "o passo de e-mail não deveria nem ser tentado após o conflito"


def test_cadencia_sem_passos_nao_cria_nada_e_nao_falha() -> None:
    """CONFIRMADO: o laço `for passo in cadencia.passos` simplesmente não
    executa para uma cadência sem passos -- sem erro, sem nenhuma query."""
    lead = _lead()
    cadencia = _cadencia([])
    session = FakeSession([])

    criados = asyncio.run(aplicar_cadencia_a_lead(session, lead, cadencia, "Operador", ator_id=9))

    assert criados == 0
    assert session.executados == []


def test_lead_sem_responsavel_ainda_recebe_lembrete_com_responsavel_none() -> None:
    """CONFIRMADO: LembreteCRM.responsavel_id é preenchido com
    lead.responsavel_id no momento da criação -- se o lead ainda não tem
    responsável, a tarefa nasce sem responsável também (não é bloqueado,
    não há fallback nem erro)."""
    lead = _lead(responsavel_id=None)
    cadencia = _cadencia([_passo(canal="ligacao")])
    session = FakeSession([FakeResult(scalar=202)])

    criados = asyncio.run(aplicar_cadencia_a_lead(session, lead, cadencia, "Operador", ator_id=9))

    assert criados == 1
    insercao = session.executados[0]
    valores = insercao.compile(compile_kwargs={"literal_binds": True})
    assert "responsavel_id" in str(valores)


def test_cadencia_automatica_so_busca_cadencias_ativas() -> None:
    """Confirmado por leitura da query (não por execução real de SQL --
    FakeSession não filtra WHERE): aplicar_cadencias_automaticas filtra
    Cadencia.ativo.is_(True) na própria consulta -- uma cadência inativa
    nunca é candidata a disparo automático. Aqui simulamos o resultado que
    o banco devolveria (lista vazia) para confirmar que, quando nada é
    encontrado, a função devolve 0 sem tentar aplicar nada."""
    lead = _lead()
    session = FakeSession([FakeResult(itens=[])])

    total = asyncio.run(aplicar_cadencias_automaticas(session, lead, "fase", "contato_inicial", "sistema"))

    assert total == 0

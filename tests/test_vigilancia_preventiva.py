import asyncio
from datetime import UTC, datetime

from app.models import (
    ClientePortal,
    ColidenciaVigilancia,
    Lead,
    PreferenciaVigilancia,
    Processo,
    VigilanciaExecucao,
)
from app.vigilancia import calcular_score_risco, executar_vigilancia_semanal, validar_comunicacao
from tests.conftest import FakeResult, FakeSession


def test_score_vigilancia_explicavel_e_deterministico():
    primeiro = calcular_score_risco("Minha Marca", "Minha Marca Ltda", {"35"}, {"01"}, {"35"}, {"01"})
    segundo = calcular_score_risco("Minha Marca", "Minha Marca Ltda", {"35"}, {"01"}, {"35"}, {"01"})
    assert primeiro == segundo
    assert primeiro[0] > 0
    assert primeiro[1]["classes_nice"] == ["35"]
    assert primeiro[1]["codigos_viena"] == ["01"]


def test_comunicacao_exige_regra_e_aprovacao():
    preferencia = PreferenciaVigilancia(ativo=True, canais=["portal"])
    item = ColidenciaVigilancia(status="pendente", evidencias={"regra": "vigilancia"}, justificativa="evidencia")
    permitido, motivo = validar_comunicacao(preferencia=preferencia, colidencia=item, canal="portal")
    assert not permitido and "aprovacao" in motivo
    item.status = "aprovado"
    item.aprovado_por = 1
    item.aprovado_em = datetime.now(UTC)
    permitido, motivo = validar_comunicacao(preferencia=preferencia, colidencia=item, canal="portal")
    assert permitido and motivo == "ok"


def test_falso_positivo_bloqueia_comunicacao():
    preferencia = PreferenciaVigilancia(ativo=True, canais=["portal"])
    item = ColidenciaVigilancia(
        status="aprovado",
        aprovado_por=1,
        aprovado_em=datetime.now(UTC),
        evidencias={"regra": "vigilancia"},
        justificativa="revisado",
        falso_positivo=True,
    )
    permitido, motivo = validar_comunicacao(preferencia=preferencia, colidencia=item, canal="portal")
    assert not permitido and "falso positivo" in motivo


# --- Achado 17.5: vigilância semanal com quebra por organização --------------
# Antes a função só devolvia o total agregado de todas as organizações (que o
# worker gravava num alerta fixo da organização 1) e acumulava os contadores
# entre as organizações -- a VigilanciaExecucao de cada uma registrava também
# os números das organizações anteriores no laço.


def _processo(id_: int, titulo: str) -> Processo:
    return Processo(id=id_, numero=f"9000{id_}", titulo=titulo, fonte="rpi", classificacoes=[], movimentacoes=[])


def test_vigilancia_semanal_devolve_quebra_por_organizacao_sem_acumular():
    session = FakeSession(
        [
            FakeResult(itens=[1, 2]),  # organizações com preferência ativa
            # organização 1
            FakeResult(scalar=None),  # sem execução nesta semana
            FakeResult(itens=[PreferenciaVigilancia(cliente_id=10, classes_nice=[], codigos_viena=[])]),
            FakeResult(itens=[_processo(100, "Minha Marca"), _processo(101, "Minha Marca Plus")]),
            FakeResult(scalar=None),  # processo 100: colidência nova
            FakeResult(scalar=ColidenciaVigilancia(id=5)),  # processo 101: já existia
            # organização 2
            FakeResult(scalar=None),
            FakeResult(itens=[PreferenciaVigilancia(cliente_id=20, classes_nice=[], codigos_viena=[])]),
            FakeResult(itens=[_processo(200, "Outra Marca")]),
            FakeResult(scalar=None),  # processo 200: colidência nova
        ],
        objetos_get=[
            ClientePortal(id=10, lead_id=1000),
            Lead(id=1000, marca="Minha Marca"),
            ClientePortal(id=20, lead_id=2000),
            Lead(id=2000, marca="Outra Marca"),
        ],
    )

    resultado = asyncio.run(executar_vigilancia_semanal(session))

    assert resultado["por_organizacao"] == {
        1: {"encontrados": 2, "criadas": 1},
        2: {"encontrados": 1, "criadas": 1},
    }
    assert resultado["encontrados"] == 3
    assert resultado["criadas"] == 2
    execucoes = {obj.organizacao_id: obj for obj in session.adicionados if isinstance(obj, VigilanciaExecucao)}
    # Cada execução registra só os números da própria organização.
    assert (execucoes[1].encontrados, execucoes[1].criados) == (2, 1)
    assert (execucoes[2].encontrados, execucoes[2].criados) == (1, 1)
    colidencias = [obj for obj in session.adicionados if isinstance(obj, ColidenciaVigilancia)]
    assert sorted((c.organizacao_id, c.processo_id) for c in colidencias) == [(1, 100), (2, 200)]


def test_vigilancia_semanal_ja_concluida_nao_entra_na_quebra():
    session = FakeSession([FakeResult(scalar=VigilanciaExecucao(organizacao_id=3, chave="x", status="concluida"))])

    resultado = asyncio.run(executar_vigilancia_semanal(session, 3))

    assert resultado["por_organizacao"] == {}
    assert resultado["criadas"] == 0

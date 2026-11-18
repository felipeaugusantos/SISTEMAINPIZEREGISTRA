import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import alertas_ativos as aa
from app.alertas_ativos import ATENCAO, CRITICO, Condicao, transicionar
from app.api import tv_monitoramento as tv
from app.database import get_session
from app.main import app
from app.settings import get_settings
from tests.conftest import FakeResult, FakeSession

# 2026-11-18 é quarta-feira. Brasília = UTC-3: 15:00 UTC = 12:00 (horário de atenção), 02:00 UTC = 23:00 (fora).
DIA = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)
NOITE = datetime(2026, 11, 19, 2, 0, tzinfo=UTC)
CRIT = Condicao("disp:erp", CRITICO, "EZRA ERP fora do ar")
ATEN = Condicao("erp:status", ATENCAO, "EZRA ERP em amarelo")


def _passar(cond, inicio, minutos, passo=1):
    estado = None
    historico = []
    for i in range(0, minutos + 1, passo):
        estado = transicionar(estado, cond, inicio + timedelta(minutes=i))
        historico.append(estado)
    return estado, historico


def test_horario_de_atencao_7_a_22_em_brasilia():
    assert aa.em_horario_de_atencao(datetime(2026, 11, 18, 10, 0, tzinfo=UTC))  # 07:00
    assert not aa.em_horario_de_atencao(datetime(2026, 11, 18, 9, 59, tzinfo=UTC))  # 06:59
    assert aa.em_horario_de_atencao(datetime(2026, 11, 19, 0, 59, tzinfo=UTC))  # 21:59
    assert not aa.em_horario_de_atencao(datetime(2026, 11, 19, 1, 0, tzinfo=UTC))  # 22:00


def test_critico_avisa_uma_vez_na_transicao_e_nao_repete_por_minuto():
    estado, hist = _passar(CRIT, DIA, 30)
    assert estado.aviso_seq == 1 and estado.aviso_tipo == "novo"
    assert {e.aviso_seq for e in hist} == {1}


def test_lembrete_a_cada_4_horas_enquanto_persiste():
    estado, _ = _passar(CRIT, DIA, 4 * 60 - 1, passo=1)
    assert estado.aviso_seq == 1
    estado = transicionar(estado, CRIT, DIA + timedelta(hours=4))
    assert estado.aviso_seq == 2 and estado.aviso_tipo == "lembrete"
    estado = transicionar(estado, CRIT, DIA + timedelta(hours=4, minutes=5))
    assert estado.aviso_seq == 2
    estado = transicionar(estado, CRIT, DIA + timedelta(hours=8))
    assert estado.aviso_seq == 3


def test_recuperacao_so_apos_2_minutos_sem_a_condicao_e_uma_vez():
    estado, _ = _passar(CRIT, DIA, 5)
    t = DIA + timedelta(minutes=6)
    estado = transicionar(estado, None, t)
    assert estado.em_falha and estado.aviso_seq == 1  # ainda na espera
    estado = transicionar(estado, None, t + timedelta(minutes=1))
    assert estado.em_falha and estado.aviso_seq == 1
    estado = transicionar(estado, None, t + timedelta(minutes=2))
    assert not estado.em_falha and estado.aviso_seq == 2 and estado.aviso_tipo == "recuperado"
    depois = transicionar(estado, None, t + timedelta(minutes=10))
    assert depois == estado


def test_oscilacao_curta_nao_gera_novo_aviso_nem_recuperacao():
    estado, _ = _passar(CRIT, DIA, 5)
    estado = transicionar(estado, None, DIA + timedelta(minutes=6))
    estado = transicionar(estado, CRIT, DIA + timedelta(minutes=7))
    assert estado.em_falha and estado.ausente_desde is None and estado.aviso_seq == 1


def test_volta_a_falhar_depois_de_recuperado_gera_aviso_novo():
    estado, _ = _passar(CRIT, DIA, 5)
    for i in range(6, 10):
        estado = transicionar(estado, None, DIA + timedelta(minutes=i))
    assert estado.aviso_tipo == "recuperado" and estado.aviso_seq == 2
    estado = transicionar(estado, CRIT, DIA + timedelta(minutes=20))
    assert estado.aviso_seq == 3 and estado.aviso_tipo == "novo"


def test_atencao_fora_do_horario_espera_e_avisa_as_7h():
    estado, _ = _passar(ATEN, NOITE, 30)
    assert estado.confirmado and estado.aviso_seq == 0 and estado.aviso_tipo is None
    sete = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    estado = transicionar(estado, ATEN, sete)
    assert estado.aviso_seq == 1 and estado.aviso_tipo == "novo"


def test_atencao_que_some_de_noite_sem_ter_avisado_nao_manda_recuperacao():
    estado, _ = _passar(ATEN, NOITE, 5)
    for i in range(6, 10):
        estado = transicionar(estado, None, NOITE + timedelta(minutes=i))
    assert not estado.em_falha and estado.aviso_seq == 0 and estado.aviso_tipo is None


def test_recuperacao_de_atencao_ja_avisada_espera_o_horario():
    estado, _ = _passar(ATEN, DIA, 5)
    assert estado.aviso_seq == 1
    t = datetime(2026, 11, 19, 1, 30, tzinfo=UTC)  # 22:30 Brasília
    estado = transicionar(estado, None, t)
    estado = transicionar(estado, None, t + timedelta(minutes=5))
    assert estado.em_falha and estado.aviso_seq == 1
    sete = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    estado = transicionar(estado, None, sete)
    assert not estado.em_falha and estado.aviso_tipo == "recuperado" and estado.aviso_seq == 2


def test_subir_para_critico_gera_aviso_novo():
    estado, _ = _passar(ATEN, DIA, 3)
    assert estado.aviso_seq == 1
    estado = transicionar(estado, Condicao(ATEN.fingerprint, CRITICO, "x"), DIA + timedelta(minutes=4))
    assert estado.severidade == CRITICO and estado.aviso_seq == 2


def test_evolution_so_alerta_apos_15_minutos_desconectado():
    cond = aa.condicao_evolution({"instance": {"state": "close"}}, None)[0]
    estado, hist = _passar(cond, DIA, 14)
    assert not estado.confirmado and estado.aviso_seq == 0
    estado = transicionar(estado, cond, DIA + timedelta(minutes=15))
    assert estado.confirmado and estado.aviso_seq == 1 and estado.severidade == CRITICO


def test_evolution_conectado_nao_gera_condicao_e_sem_resposta_gera():
    assert aa.condicao_evolution({"instance": {"instanceName": "x", "state": "open"}}, None) == []
    assert aa.condicao_evolution(None, "ConnectError")[0].fingerprint == "evolution:sessao"
    assert aa.condicao_evolution(["lixo"], None)[0].severidade == CRITICO


def test_condicoes_de_disponibilidade_so_com_incidente_aberto():
    disp = {"alvos": {"zeregistra": {"incidente_aberto": False}, "erp": {"incidente_aberto": True}}}
    assert [c.fingerprint for c in aa.condicoes_disponibilidade(disp)] == ["disp:erp"]


def test_condicoes_de_backup_da_vps_so_etapas_criticas():
    snap = {
        "etapas": [
            {"chave": "dump", "status": "critico", "detalhe": "falhou"},
            {"chave": "offsite", "status": "ok"},
            {"chave": "disco", "status": "critico"},
        ]
    }
    assert [c.fingerprint for c in aa.condicoes_backup_vps(snap)] == ["backup:dump"]


def test_condicoes_do_erp_status_e_backup_de_cliente():
    erp = {
        "saude": "amarelo",
        "motivo": "1 erro(s) novo(s) na última hora",
        "backup": {"disponivel": True, "saude": "amarelo", "clientes_ativos": 5, "em_dia": 4, "atrasados": 1},
    }
    conds = aa.condicoes_erp(erp)
    assert {c.fingerprint for c in conds} == {"erp:status", "erp:backup"}
    assert all(c.severidade == ATENCAO for c in conds)
    assert aa.condicoes_erp({"saude": "verde", "backup": {"disponivel": True, "saude": "verde"}}) == []


def test_status_central_e_erros_recorrentes_seguem_os_limiares_da_tv():
    assert aa.condicoes_status_central(0) == []
    assert "amarelo" in aa.condicoes_status_central(3)[0].titulo
    assert "vermelho" in aa.condicoes_status_central(10)[0].titulo
    conds = aa.condicoes_erros_recorrentes([("GET /x", 500, 3), ("GET /y", 502, 2)])
    assert [c.fingerprint for c in conds] == ["erros:GET /x|500"]


@pytest.mark.asyncio
async def test_fonte_nao_avaliada_nao_recupera_alerta_existente():
    from app.models import AlertaEstado

    linha = AlertaEstado(
        fingerprint="disp:erp",
        severidade=CRITICO,
        titulo="t",
        detalhe="",
        desde_quando=DIA,
        em_falha=True,
        confirmado=True,
        aviso_seq=1,
        ultimo_aviso=DIA,
        aviso_tipo="novo",
    )

    class Sessao:
        def __init__(self):
            self.executados = []
            self.adicionados = []

        async def execute(self, consulta):
            self.executados.append(consulta)
            return FakeResult(itens=[linha])

        def add(self, obj):
            self.adicionados.append(obj)

    # Sem "disp:" entre as fontes avaliadas: a linha fica exatamente como estava.
    sessao = Sessao()
    await aa.aplicar_condicoes(sessao, [], {"zr:"}, DIA + timedelta(hours=1))
    assert linha.em_falha and linha.aviso_seq == 1 and linha.aviso_tipo == "novo"


CHAVE = "chave-de-tv-para-teste-com-bastante-entropia-123"
HASH = hashlib.sha256(CHAVE.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _limpar():
    yield
    app.dependency_overrides.clear()


def _cliente(monkeypatch, linhas):
    config = get_settings().model_copy(update={"tv_monitoramento_token_sha256": HASH})
    monkeypatch.setattr(tv, "get_settings", lambda: config)

    async def _avaliado():
        return "2026-11-18T15:00:00+00:00"

    monkeypatch.setattr(tv, "ler_avaliador_em", _avaliado)
    sessao = FakeSession([FakeResult(itens=linhas)])

    async def _gen():
        yield sessao

    app.dependency_overrides[get_session] = _gen
    return TestClient(app)


def test_endpoint_alertas_exige_a_chave_da_tv(monkeypatch):
    cliente = _cliente(monkeypatch, [])
    assert cliente.get("/v1/monitoramento/alertas").status_code == 401
    assert cliente.get("/v1/monitoramento/alertas", headers={"Authorization": "Bearer errada"}).status_code == 401


def test_endpoint_alertas_devolve_so_campos_conhecidos(monkeypatch):
    from app.models import AlertaEstado

    linha = AlertaEstado(
        fingerprint="disp:erp",
        severidade=CRITICO,
        titulo="EZRA ERP fora do ar",
        detalhe="Sonda sem resposta.",
        desde_quando=DIA,
        em_falha=True,
        confirmado=True,
        aviso_seq=2,
        ultimo_aviso=DIA,
        aviso_tipo="lembrete",
    )
    cliente = _cliente(monkeypatch, [linha])
    resposta = cliente.get("/v1/monitoramento/alertas", headers={"Authorization": f"Bearer {CHAVE}"})
    assert resposta.status_code == 200
    assert resposta.headers["cache-control"] == "no-store"
    corpo = resposta.json()
    assert corpo["avaliado_em"] == "2026-11-18T15:00:00+00:00"
    (item,) = corpo["alertas"]
    assert set(item) == {
        "fingerprint",
        "severidade",
        "titulo",
        "detalhe",
        "desde_quando",
        "ativo",
        "aviso_seq",
        "aviso_tipo",
        "ultimo_aviso",
    }
    assert item["ativo"] is True and item["aviso_seq"] == 2 and item["aviso_tipo"] == "lembrete"


def test_endpoint_alertas_so_aceita_get(monkeypatch):
    cliente = _cliente(monkeypatch, [])
    assert cliente.post("/v1/monitoramento/alertas", headers={"Authorization": f"Bearer {CHAVE}"}).status_code == 405

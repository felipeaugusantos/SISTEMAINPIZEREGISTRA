from datetime import UTC, datetime, timedelta

import pytest

from app.rpi.health import avaliar_saude_rpi

AGORA = datetime(2026, 10, 30, 16, tzinfo=UTC)
ULTIMA = datetime(2026, 10, 27, 17, 38, tzinfo=UTC)
PROXIMA = datetime(2026, 11, 3, 13, tzinfo=UTC)


def avaliar(**alteracoes):
    dados = dict(
        status_sync="atualizado", ultima_rpi_oficial=2912, ultima_rpi_importada=2912,
        ultima_sincronizacao=ULTIMA, status_integridade="ok", limite_atraso_horas=12,
        proxima_verificacao_em=PROXIMA, heartbeat_em=AGORA - timedelta(seconds=8), agora=AGORA,
    )
    return avaliar_saude_rpi(**{**dados, **alteracoes})


def test_agenda_semanal_ativa_nao_e_atraso_apos_doze_horas():
    status, idade, motivos = avaliar()
    assert status == "ok"
    assert idade > 70
    assert motivos == []


@pytest.mark.parametrize("alteracoes", [
    {"proxima_verificacao_em": None},
    {"proxima_verificacao_em": ULTIMA},
    {"proxima_verificacao_em": ULTIMA - timedelta(hours=1)},
    {"proxima_verificacao_em": ULTIMA + timedelta(days=30)},
    {"heartbeat_em": None},
    {"heartbeat_em": AGORA - timedelta(seconds=61)},
    {"heartbeat_em": AGORA + timedelta(seconds=1)},
    {"status_sync": "iniciando"},
])
def test_agenda_sem_comprovacao_nao_oculta_atraso(alteracoes):
    assert avaliar(**alteracoes)[0] == "atrasado"


@pytest.mark.parametrize("deslocamento,status", [(0, "ok"), (1, "atrasado")])
def test_prazo_agendado_mais_tolerancia(deslocamento, status):
    agora = PROXIMA + timedelta(hours=12, seconds=deslocamento)
    assert avaliar(agora=agora, heartbeat_em=agora)[0] == status


@pytest.mark.parametrize("alteracoes,status", [
    ({"ultima_rpi_oficial": 2913}, "atrasado"),
    ({"status_sync": "falhou"}, "erro"),
    ({"status_integridade": "erro"}, "erro"),
    ({"ultima_rpi_importada": None}, "erro"),
    ({"ultima_sincronizacao": None}, "erro"),
    ({"status_sync": "importando"}, "processando"),
])
def test_agenda_preserva_sinais_reais_de_problema_e_processamento(alteracoes, status):
    assert avaliar(**alteracoes)[0] == status


def test_datas_legadas_sem_fuso_sao_interpretadas_como_utc():
    assert avaliar(
        ultima_sincronizacao=ULTIMA.replace(tzinfo=None),
        proxima_verificacao_em=PROXIMA.replace(tzinfo=None),
        heartbeat_em=AGORA.replace(tzinfo=None),
    )[0] == "ok"


def test_heartbeat_respeita_intervalo_configurado_do_processo():
    assert avaliar(heartbeat_em=AGORA - timedelta(seconds=90), limite_heartbeat_segundos=120)[0] == "ok"

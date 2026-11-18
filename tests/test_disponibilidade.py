import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.disponibilidade import coletar_disponibilidade

# Disponibilidade do mês e histórico de incidentes (painel de TV, 17/11/2026):
# estes testes travam o que conta como minuto fora, o que vira incidente e
# como o mês parcial (coleta começando no meio) é calculado.

AGORA = datetime(2026, 11, 17, 22, 0, tzinfo=UTC)  # 19:00 em Brasília


def _linha(alvo: str, minutos_atras: float, ok: bool = True) -> str:
    em = (AGORA - timedelta(minutes=minutos_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"alvo": alvo, "ok": ok, "em": em, "ms": 120, "http": 200 if ok else 503})


def _raw(diretorio: Path, *linhas: str) -> None:
    (diretorio / "disponibilidade.jsonl").write_text("\n".join(linhas) + "\n", encoding="utf-8")


def _diario(diretorio: Path, *linhas: str) -> None:
    (diretorio / "disponibilidade-diario.jsonl").write_text("\n".join(linhas) + "\n", encoding="utf-8")


def _dia(alvo: str, dia: str, up: int) -> str:
    return json.dumps({"alvo": alvo, "dia": dia, "minutos_up": up})


def test_sem_arquivos_fica_indisponivel_e_cinza(tmp_path: Path) -> None:
    resultado = coletar_disponibilidade(tmp_path, agora=AGORA)
    for alvo in ("zeregistra", "erp"):
        assert resultado["alvos"][alvo] == {
            "disponivel": False,
            "incidente_aberto": False,
            "saude": "cinza",
        }
    assert resultado["incidentes"] == []
    assert resultado["mes"] == "2026-11"


def test_mes_soma_diario_e_raw_de_hoje(tmp_path: Path) -> None:
    _diario(
        tmp_path,
        _dia("zeregistra", "2026-11-15", 1410),  # 30 min fora
        _dia("zeregistra", "2026-11-16", 1440),
    )
    _raw(tmp_path, *[_linha("zeregistra", m) for m in range(1, 1141)])  # hoje inteiro ok
    zr = coletar_disponibilidade(tmp_path, agora=AGORA)["alvos"]["zeregistra"]
    # base = 2 dias + minutos de hoje desde 00:00 Brasília até o minuto anterior
    assert zr["minutos_mes"] == 2 * 1440 + 19 * 60
    assert zr["minutos_fora_mes"] == 30
    assert zr["coletando_desde"] == "2026-11-15"
    assert zr["saude"] == "amarelo"  # 3990/4020 = ~99,25%


def test_dia_anterior_ao_inicio_da_coleta_nao_conta(tmp_path: Path) -> None:
    _diario(tmp_path, _dia("zeregistra", "2026-11-17", 0))  # dia de hoje no diário é ignorado
    _raw(tmp_path, *[_linha("zeregistra", m) for m in range(1, 31)])
    zr = coletar_disponibilidade(tmp_path, agora=AGORA)["alvos"]["zeregistra"]
    # coleta começou hoje: só os minutos de hoje entram; 1/11 a 16/11 ficam de fora
    assert zr["coletando_desde"] == "2026-11-17"
    assert zr["minutos_mes"] == 19 * 60


def test_dia_coberto_sem_agregado_e_fora_da_janela_raw_conta_100_por_cento_fora(tmp_path: Path) -> None:
    # coleta começou dia 10 (raw já não chega lá) e o dia 12 não tem fechamento
    _diario(tmp_path, _dia("zeregistra", "2026-11-10", 1440), _dia("zeregistra", "2026-11-11", 1440))
    _raw(tmp_path, *[_linha("zeregistra", m) for m in range(1, 31)])
    zr = coletar_disponibilidade(tmp_path, agora=AGORA)["alvos"]["zeregistra"]
    assert zr["minutos_fora_mes"] >= 5 * 1440  # dias 12 a 16 sem dado contam como fora


def test_queda_de_um_minuto_nao_vira_incidente(tmp_path: Path) -> None:
    _raw(tmp_path, *[_linha("zeregistra", m, ok=(m != 30)) for m in range(1, 61)])
    resultado = coletar_disponibilidade(tmp_path, agora=AGORA)
    assert resultado["incidentes"] == []
    assert resultado["alvos"]["zeregistra"]["incidente_aberto"] is False


def test_queda_de_5_minutos_vira_incidente_fechado(tmp_path: Path) -> None:
    _raw(tmp_path, *[_linha("zeregistra", m, ok=not (20 <= m <= 24)) for m in range(1, 61)])
    resultado = coletar_disponibilidade(tmp_path, agora=AGORA)
    assert len(resultado["incidentes"]) == 1
    incidente = resultado["incidentes"][0]
    assert incidente["alvo"] == "zeregistra"
    assert incidente["tipo"] == "falha"
    assert incidente["minutos"] == 5
    assert incidente["fim"] is not None


def test_minutos_sem_linha_no_fim_abrem_incidente_sem_dados(tmp_path: Path) -> None:
    _raw(tmp_path, *[_linha("zeregistra", m) for m in range(4, 61)])  # últimos 3 min sem linha
    resultado = coletar_disponibilidade(tmp_path, agora=AGORA)
    zr = resultado["alvos"]["zeregistra"]
    assert zr["incidente_aberto"] is True
    assert zr["saude"] == "vermelho"
    assert resultado["incidentes"][0]["tipo"] == "sem_dados"
    assert resultado["incidentes"][0]["fim"] is None


def test_minuto_ok_solto_no_meio_nao_encerra_incidente(tmp_path: Path) -> None:
    linhas = []
    for m in range(1, 31):
        ok = not (10 <= m <= 20) or m == 15  # 11 min fora com 1 min ok no meio
        linhas.append(_linha("zeregistra", m, ok=ok))
    _raw(tmp_path, *linhas)
    resultado = coletar_disponibilidade(tmp_path, agora=AGORA)
    assert len(resultado["incidentes"]) == 1
    assert resultado["incidentes"][0]["minutos"] == 11


def test_virada_de_mes_ignora_agregado_do_mes_anterior(tmp_path: Path) -> None:
    agora = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)  # 09:00 em Brasília, 1/12
    _diario(tmp_path, _dia("zeregistra", "2026-11-30", 1000))
    _raw(
        tmp_path,
        *[
            json.dumps(
                {
                    "alvo": "zeregistra",
                    "ok": True,
                    "em": (agora - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
            for m in range(1, 541)
        ],
    )
    resultado = coletar_disponibilidade(tmp_path, agora=agora)
    zr = resultado["alvos"]["zeregistra"]
    assert resultado["mes"] == "2026-12"
    assert zr["minutos_mes"] == 9 * 60  # só o que passou de dezembro
    assert zr["coletando_desde"] == "2026-12-01"
    assert zr["disponibilidade_mes"] == 1.0
    assert zr["saude"] == "verde"


def test_linhas_invalidas_sao_ignoradas(tmp_path: Path) -> None:
    _raw(tmp_path, "lixo", json.dumps({"alvo": "outro", "ok": True, "em": "2026-11-17T21:00:00Z"}),
         json.dumps({"alvo": "zeregistra", "ok": True, "em": "não é data"}))
    resultado = coletar_disponibilidade(tmp_path, agora=AGORA)
    assert resultado["alvos"]["zeregistra"]["disponivel"] is False

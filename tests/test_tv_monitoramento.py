import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import tv_monitoramento as tv
from app.api.tv_monitoramento import _dias_da_serie
from app.database import get_session
from app.main import app
from app.settings import get_settings
from tests.conftest import FakeResult, FakeSession

# Painel de TV (17/11/2026): leitura agregada dos dois sistemas com chave própria,
# sem sessão de usuário. Estes testes travam o que a TV pode e não pode ver.

CHAVE = "chave-de-tv-para-teste-com-bastante-entropia-123"
HASH = hashlib.sha256(CHAVE.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _sem_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _nada() -> None:
        return None

    monkeypatch.setattr(tv, "ler_snapshot", _nada)


@pytest.fixture(autouse=True)
def _limpar() -> None:
    yield
    app.dependency_overrides.clear()
    tv._erp.dados = None
    tv._erp.obtido_em = None
    tv._erp.consultado_em = 0.0
    tv._erp.falhando_desde = None


def _configurar(monkeypatch: pytest.MonkeyPatch, **extra) -> None:
    config = get_settings().model_copy(update={"tv_monitoramento_token_sha256": HASH, **extra})
    monkeypatch.setattr(tv, "get_settings", lambda: config)


def _sessao() -> FakeSession:
    _, dias = _dias_da_serie(7)
    session = FakeSession(
        [
            FakeResult(itens=[(5000, 40)]),  # requisições, erros (24 h)
            FakeResult(scalar=3),  # erros 5xx na última hora
            FakeResult(itens=[(120, 7, 3)]),  # ações, usuários, organizações
            FakeResult(itens=[("PATCH /v1/admin/crm/lembretes/{id}", 429, 30), ("GET /x", 500, 2)]),  # top erros
            FakeResult(itens=[(dias[-1], 120), (dias[-2], 80)]),  # ações por dia
            FakeResult(itens=[(dias[-1], 40)]),  # erros por dia
        ]
    )

    async def _gen():
        yield session

    app.dependency_overrides[get_session] = _gen
    return session


def _cliente() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_painel_desligado_sem_hash_configurado_responde_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch, tv_monitoramento_token_sha256="")
    resposta = _cliente().get("/v1/monitoramento/tv", headers={"Authorization": f"Bearer {CHAVE}"})
    assert resposta.status_code == 404


@pytest.mark.parametrize("cabecalho", [None, "", "Bearer ", "Bearer chave-errada", "Basic abc", CHAVE])
def test_chave_ausente_ou_errada_responde_401(monkeypatch: pytest.MonkeyPatch, cabecalho: str | None) -> None:
    _configurar(monkeypatch)
    _sessao()
    headers = {"Authorization": cabecalho} if cabecalho is not None else {}
    resposta = _cliente().get("/v1/monitoramento/tv", headers=headers)
    assert resposta.status_code == 401
    assert resposta.headers.get("cache-control") != "public"


def test_chave_correta_devolve_resumo_agregado_sem_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch)
    _sessao()
    resposta = _cliente().get("/v1/monitoramento/tv", headers={"Authorization": f"Bearer {CHAVE}"})
    assert resposta.status_code == 200
    assert resposta.headers["cache-control"] == "no-store"
    corpo = resposta.json()
    central = corpo["zeregistra"]
    assert central["agregado"] is True
    assert central["saude"] == "amarelo"  # 3 erros 5xx na última hora
    assert central["erros_5xx_1h"] == 3
    assert central["requisicoes_24h"] == 5000
    assert central["erros_24h"] == 40
    assert central["taxa_erros_24h"] == pytest.approx(40 / 5000)
    assert central["usuarios_ativos_24h"] == 7
    assert central["organizacoes_ativas_24h"] == 3
    assert len(central["serie"]) == 7
    assert central["serie"][-1]["acoes"] == 120 and central["serie"][-1]["erros"] == 40
    assert central["serie"][0]["acoes"] == 0
    assert central["top_erros"][0] == {"operacao": "PATCH /v1/admin/crm/lembretes/{id}", "status_http": 429, "quantidade": 30}
    assert corpo["erp"]["status"] == "nao_configurado"
    # nada que identifique organização, usuário ou e-mail
    texto = resposta.text.lower()
    for proibido in ("nome", "email", "usuario\"", "ip\""):
        assert proibido not in texto


def test_saude_por_erros_de_servidor() -> None:
    assert tv.saude_por_erros(0) == "verde"
    assert tv.saude_por_erros(1) == "amarelo"
    assert tv.saude_por_erros(9) == "amarelo"
    assert tv.saude_por_erros(10) == "vermelho"


def test_normalizar_erp_copia_so_campos_conhecidos() -> None:
    bruto = {
        "gerado_em": "2026-11-17T10:00:00Z",
        "erros_abertos": 4,
        "erros_novos_1h": 12,
        "erros_novos_24h": 20,
        "usuarios_ativos_24h": 9,
        "acoes_24h": 300,
        "clientes_ativos_24h": 3,
        "clientes_sem_uso_7d": 1,
        "cliente": "Clínica Secreta",
        "serie": [{"dia": "2026-11-17", "erros_novos": 2, "acoes": 50, "cliente": "Loja X"}],
        "top_erros": [{"rota": "/caixa", "origem": "navegador", "ocorrencias": 7, "mensagem": "a@b.com", "cliente": "Y"}],
    }
    r = tv.normalizar_erp(bruto)
    assert r["saude"] == "vermelho"
    assert r["motivo"] == "12 erros novos na última hora"
    assert r["serie"] == [{"dia": "2026-11-17", "acoes": 50, "erros": 2}]
    assert r["top_erros"] == [{"rota": "/caixa", "origem": "navegador", "quantidade": 7}]
    assert "Secreta" not in str(r) and "Loja" not in str(r) and "a@b.com" not in str(r)


def test_normalizar_erp_lista_clientes_sem_uso_so_nome_e_dias() -> None:
    bruto = {
        "clientes_sem_uso": [
            {"nome": "  Loja Parada  ", "dias_sem_uso": 12, "email": "a@b.com", "documento": "12.345"},
            {"nome": "Clínica Nova", "dias_sem_uso": None},
            {"nome": "X" * 80, "dias_sem_uso": "9"},
            {"nome": "   ", "dias_sem_uso": 4},
            "lixo",
            {"dias_sem_uso": 7},
        ]
    }
    r = tv.normalizar_erp(bruto)
    assert r["clientes_sem_uso"] == [
        {"nome": "Loja Parada", "dias_sem_uso": 12},
        {"nome": "Clínica Nova", "dias_sem_uso": None},
        {"nome": "X" * 60, "dias_sem_uso": 9},
    ]
    assert "a@b.com" not in str(r) and "12.345" not in str(r)


def test_normalizar_erp_lista_clientes_sem_uso_invalida() -> None:
    assert tv.normalizar_erp({"clientes_sem_uso": "lixo"})["clientes_sem_uso"] == []
    assert tv.normalizar_erp({})["clientes_sem_uso"] == []
    muitos = [{"nome": f"Cliente {i}", "dias_sem_uso": i} for i in range(30)]
    assert len(tv.normalizar_erp({"clientes_sem_uso": muitos})["clientes_sem_uso"]) == 20


def test_inteiro_ou_nulo() -> None:
    assert tv._inteiro_ou_nulo(None) is None
    assert tv._inteiro_ou_nulo(-1) is None
    assert tv._inteiro_ou_nulo(0) == 0
    assert tv._inteiro_ou_nulo("7") == 7
    assert tv._inteiro_ou_nulo("lixo") is None


def test_normalizar_erp_com_entrada_invalida_vira_zeros() -> None:
    r = tv.normalizar_erp("lixo")
    assert r["erros_abertos"] == 0 and r["serie"] == [] and r["top_erros"] == []
    # tudo zero = ERP sem uso: nunca pode dizer "Saudável"
    assert r["saude"] == "amarelo" and r["motivo"] == "Sem uso nas últimas 24 h"


@pytest.mark.parametrize(
    ("campos", "saude", "motivo"),
    [
        ({"acoes_24h": 5}, "verde", None),
        ({"acoes_24h": 5, "erros_abertos": 2}, "amarelo", "2 erro(s) aberto(s) para revisar"),
        ({"acoes_24h": 0, "usuarios_ativos_24h": 0}, "amarelo", "Sem uso nas últimas 24 h"),
        ({"acoes_24h": 5, "erros_novos_1h": 3}, "amarelo", "3 erro(s) novo(s) na última hora"),
        ({"acoes_24h": 5, "erros_abertos": 7, "erros_novos_1h": 10}, "vermelho", "10 erros novos na última hora"),
    ],
)
def test_normalizar_erp_saude_honesta_com_motivo(campos: dict, saude: str, motivo: str | None) -> None:
    r = tv.normalizar_erp(campos)
    assert r["saude"] == saude
    assert r["motivo"] == motivo


def _cliente_http_falso(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    chamadas: list[httpx.Request] = []
    real = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return handler(request)

    monkeypatch.setattr(tv.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(_handler), **kw))
    return chamadas


async def test_erp_ok_usa_chave_de_leitura_e_guarda_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch, erp_tv_url="https://erp.exemplo/api/public/tv/monitoramento", erp_tv_token="chave-do-erp")
    chamadas = _cliente_http_falso(monkeypatch, lambda r: httpx.Response(200, json={"erros_novos_1h": 0, "acoes_24h": 5}))

    primeira = await tv.resumo_erp()
    segunda = await tv.resumo_erp()

    assert primeira["status"] == "ok" and primeira["dados"]["acoes_24h"] == 5 and primeira["dados"]["saude"] == "verde"
    assert segunda["status"] == "ok"
    assert len(chamadas) == 1  # a segunda veio do cache de 30 s
    assert chamadas[0].headers["authorization"] == "Bearer chave-do-erp"


async def test_erp_fora_do_ar_mantem_ultimo_dado_e_marca_indisponivel(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch, erp_tv_url="https://erp.exemplo/x", erp_tv_token="chave-do-erp")
    estado = {"ok": True}

    def _handler(request: httpx.Request) -> httpx.Response:
        if estado["ok"]:
            return httpx.Response(200, json={"acoes_24h": 9})
        raise httpx.ConnectError("sem rota", request=request)

    _cliente_http_falso(monkeypatch, _handler)
    bom = await tv.resumo_erp()
    assert bom["status"] == "ok"

    estado["ok"] = False
    tv._erp.consultado_em = 0.0  # expira o cache
    ruim = await tv.resumo_erp()
    assert ruim["status"] == "indisponivel"
    assert ruim["dados"]["acoes_24h"] == 9  # último valor bom
    assert isinstance(ruim["indisponivel_desde"], datetime) and ruim["indisponivel_desde"].tzinfo == UTC

    estado["ok"] = True
    tv._erp.consultado_em = 0.0
    volta = await tv.resumo_erp()
    assert volta["status"] == "ok" and volta["indisponivel_desde"] is None


async def test_erp_resposta_401_ou_500_nao_derruba_o_painel(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar(monkeypatch, erp_tv_url="https://erp.exemplo/x", erp_tv_token="chave-do-erp")
    _cliente_http_falso(monkeypatch, lambda r: httpx.Response(401))
    resultado = await tv.resumo_erp()
    assert resultado["status"] == "indisponivel" and resultado["dados"] is None


def test_pagina_da_tv_e_so_a_casca_sem_dado_e_sem_cache() -> None:
    resposta = _cliente().get("/tv/monitoramento")
    assert resposta.status_code == 200
    assert resposta.headers["cache-control"] == "no-store"
    assert "noindex" in resposta.text
    assert "style=" not in resposta.text  # CSP: style-src 'self'


def _etapa(horas: float, status: str = "ok", detalhe: str = "") -> dict:
    ultima = datetime.now(UTC) - timedelta(hours=horas)
    return {"ultima_em": ultima.isoformat(), "status": status, "detalhe": detalhe}


@pytest.mark.parametrize(
    ("horas", "saude"),
    [(5, "verde"), (25.9, "verde"), (27, "amarelo"), (49, "amarelo"), (51, "vermelho")],
)
def test_saude_do_backup_por_idade(horas: float, saude: str) -> None:
    assert tv._saude_backup(_etapa(horas), datetime.now(UTC))["saude"] == saude


def test_saude_do_backup_falha_ou_sem_dado() -> None:
    agora = datetime.now(UTC)
    assert tv._saude_backup(_etapa(2, "critico", "A última execução do backup falhou."), agora)["saude"] == "vermelho"
    assert tv._saude_backup(None, agora) == {"ultima_em": None, "saude": "cinza"}
    assert tv._saude_backup({"ultima_em": "lixo"}, agora)["saude"] == "cinza"


async def test_resumo_backup_so_expoe_data_e_cor(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _snapshot() -> dict:
        return {
            "etapas": [
                {**_etapa(6), "chave": "dump", "tamanho_bytes": 5, "detalhe": "Há 6 h."},
                {**_etapa(30), "chave": "offsite"},
                {**_etapa(1), "chave": "disco"},
            ]
        }

    monkeypatch.setattr(tv, "ler_snapshot", _snapshot)
    resumo = await tv.resumo_backup()
    assert resumo["disponivel"] is True
    assert set(resumo) == {"disponivel", "banco_local", "copia_externa"}
    assert set(resumo["banco_local"]) == {"ultima_em", "saude"}
    assert resumo["banco_local"]["saude"] == "verde"
    assert resumo["copia_externa"]["saude"] == "amarelo"


async def test_resumo_backup_sem_redis_nao_derruba_a_tv(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _quebra() -> dict:
        raise RuntimeError("redis fora")

    monkeypatch.setattr(tv, "ler_snapshot", _quebra)
    resumo = await tv.resumo_backup()
    assert resumo["disponivel"] is False
    assert resumo["banco_local"]["saude"] == "cinza"


def test_backup_erp_so_contagens_e_datas() -> None:
    agora = datetime.now(UTC)
    ontem = (agora - timedelta(hours=5)).isoformat()
    r = tv.normalizar_backup_erp(
        {
            "clientes_ativos": 5,
            "em_dia": 5,
            "atrasados": 0,
            "sem_backup": 0,
            "com_erro": 0,
            "ultimo_em": ontem,
            "mais_antigo_em": ontem,
            "caminho": "geral/abc/2026-11-17.ndjson.gz",
            "cliente": "Clínica Secreta",
        },
        agora,
    )
    assert r["saude"] == "verde" and r["em_dia"] == 5
    assert "caminho" not in r and "cliente" not in r
    assert "ndjson" not in str(r) and "Secreta" not in str(r)


@pytest.mark.parametrize(
    ("campos", "saude"),
    [
        ({"clientes_ativos": 5, "em_dia": 4, "atrasados": 1}, "amarelo"),
        ({"clientes_ativos": 5, "em_dia": 4, "sem_backup": 1}, "amarelo"),
        ({"clientes_ativos": 5, "em_dia": 4, "atrasados": 1, "com_erro": 1}, "vermelho"),
        ({"clientes_ativos": 0}, "cinza"),
    ],
)
def test_backup_erp_saude(campos: dict, saude: str) -> None:
    assert tv.normalizar_backup_erp(campos)["saude"] == saude


def test_backup_erp_muito_atrasado_fica_vermelho() -> None:
    agora = datetime.now(UTC)
    antigo = (agora - timedelta(hours=60)).isoformat()
    r = tv.normalizar_backup_erp({"clientes_ativos": 2, "em_dia": 1, "atrasados": 1, "mais_antigo_em": antigo}, agora)
    assert r["saude"] == "vermelho"


def test_backup_erp_ausente_ou_lixo() -> None:
    assert tv.normalizar_backup_erp(None) == {"disponivel": False, "saude": "cinza"}
    assert tv.normalizar_backup_erp({"clientes_ativos": 1, "ultimo_em": "ontem"})["ultimo_em"] is None
    assert tv.normalizar_erp({})["backup"]["disponivel"] is False

@pytest.mark.parametrize(
    ("percentual", "saude"),
    [(10, "verde"), (79.9, "verde"), (80, "amarelo"), (89.9, "amarelo"), (90, "vermelho"), (99, "vermelho")],
)
def test_sistema_limites_de_saude(percentual: float, saude: str) -> None:
    assert tv._saude_percentual(percentual) == saude


def test_sistema_so_percentuais_e_gb_livres(monkeypatch: pytest.MonkeyPatch) -> None:
    resumo = tv.resumo_sistema()
    assert set(resumo) == {"cpu", "memoria", "disco"}
    assert set(resumo["disco"]) == {"percentual", "livre_gb", "saude"}
    assert set(resumo["cpu"]) == {"percentual", "saude"}
    for leitura in resumo.values():
        assert leitura["saude"] in {"verde", "amarelo", "vermelho", "cinza"}


def test_sistema_disco_ilegivel_fica_cinza(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falha(_caminho: str) -> None:
        raise OSError("sem disco")

    monkeypatch.setattr(tv.shutil, "disk_usage", _falha)
    assert tv._leitura_disco() == {"percentual": None, "livre_gb": None, "saude": "cinza"}


def test_sistema_cpu_pela_carga_de_5_minutos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tv.os, "cpu_count", lambda: 4)
    monkeypatch.setattr("builtins.open", lambda *_a, **_k: __import__("io").StringIO("3.70 3.60 2.00 1/400 99\n"))
    leitura = tv._leitura_cpu()
    assert leitura == {"percentual": 90, "saude": "vermelho"}

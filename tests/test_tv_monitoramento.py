import hashlib
from datetime import UTC, datetime

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
    assert r["serie"] == [{"dia": "2026-11-17", "acoes": 50, "erros": 2}]
    assert r["top_erros"] == [{"rota": "/caixa", "origem": "navegador", "quantidade": 7}]
    assert "Secreta" not in str(r) and "Loja" not in str(r) and "a@b.com" not in str(r)


def test_normalizar_erp_com_entrada_invalida_vira_zeros() -> None:
    r = tv.normalizar_erp("lixo")
    assert r["erros_abertos"] == 0 and r["saude"] == "verde" and r["serie"] == [] and r["top_erros"] == []


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

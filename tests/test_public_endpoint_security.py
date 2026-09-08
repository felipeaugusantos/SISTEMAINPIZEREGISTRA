from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.confiabilidade import branding_publico
from app.api.pesquisas import limitar_relatorios
from app.database import get_session
from app.main import app
from app.models import PesquisaMarca
from app.public_report_tokens import TokenRelatorioInvalido, emitir_token_relatorio, validar_token_relatorio
from tests.conftest import FakeResult, FakeSession


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/v1/tenant/branding",
            "query_string": b"",
            "headers": [(b"host", host.encode("ascii"))],
            "server": (host, 443),
            "client": ("127.0.0.1", 1234),
        }
    )


def _tenant(organizacao_id: int, nome: str, cor: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=organizacao_id,
        nome=nome,
        slug=f"tenant-{organizacao_id}",
        status="ativa",
        plano=SimpleNamespace(codigo="profissional", modulos=["consulta"], limites={}),
        modulos_liberados=None,
        politica_privacidade_versao="2.0",
        branding={
            "nome_exibido": nome,
            "cor_primaria": cor,
            "logo_url": f"/static/{organizacao_id}.png",
            "clicksign": {"api_token_enc": "nao-pode-vazar"},
            "proposta": {"titulo": "interno"},
            "proposta_template": {"arquivo": "interno.pdf"},
            "configuracao_comercial": {"margem": 10},
        },
    )


@pytest.mark.asyncio
async def test_branding_publico_expoe_somente_allowlist_e_isola_organizacoes() -> None:
    async def consultar(organizacao_id: int, host: str, nome: str, cor: str) -> dict:
        dominio = SimpleNamespace(organizacao_id=organizacao_id)
        session = FakeSession(
            resultados=[
                FakeResult(scalar=dominio),
                FakeResult(scalar=_tenant(organizacao_id, nome, cor)),
            ]
        )
        return await branding_publico(_request(host), session)

    primeiro = await consultar(41, "tenant-41.test", "Tenant 41", "#112233")
    segundo = await consultar(99, "tenant-99.test", "Tenant 99", "#AABBCC")

    campos_publicos = {
        "nome",
        "nome_exibido",
        "cor_primaria",
        "logo_url",
        "politica_privacidade_versao",
    }
    assert set(primeiro) == campos_publicos
    assert set(segundo) == campos_publicos
    assert primeiro["nome_exibido"] == "Tenant 41"
    assert segundo["nome_exibido"] == "Tenant 99"
    assert primeiro["cor_primaria"] != segundo["cor_primaria"]
    assert "branding" not in primeiro
    assert "chave_integracao" not in primeiro


def test_token_temporario_e_vinculado_ao_relatorio_e_organizacao() -> None:
    token = emitir_token_relatorio("pesquisa-1", 41, agora=1_000, ttl_segundos=60)

    validar_token_relatorio(token, "pesquisa-1", 41, agora=1_059)
    with pytest.raises(TokenRelatorioInvalido, match="este relatório"):
        validar_token_relatorio(token, "pesquisa-2", 41, agora=1_059)
    with pytest.raises(TokenRelatorioInvalido, match="este relatório"):
        validar_token_relatorio(token, "pesquisa-1", 99, agora=1_059)


def test_token_temporario_expirado_e_rejeitado() -> None:
    token = emitir_token_relatorio("pesquisa-1", 41, agora=1_000, ttl_segundos=60)

    with pytest.raises(TokenRelatorioInvalido, match="expirado"):
        validar_token_relatorio(token, "pesquisa-1", 41, agora=1_060)


def test_relatorio_publico_possui_rate_limit() -> None:
    limite_anterior = limitar_relatorios.limite
    limitar_relatorios.limite = 1
    app.dependency_overrides[get_session] = _session_override(FakeSession())
    try:
        cliente = TestClient(app)
        assert cliente.get("/v1/pesquisas-marca/alvo/relatorio").status_code == 401
        resposta = cliente.get("/v1/pesquisas-marca/alvo/relatorio")
    finally:
        limitar_relatorios.limite = limite_anterior
        app.dependency_overrides.clear()

    assert resposta.status_code == 429
    assert int(resposta.headers["retry-after"]) >= 1


def _session_override(session: FakeSession):
    async def override():
        yield session

    return override


def test_fluxo_publico_emite_token_curto_e_nao_usa_segredo_global(monkeypatch) -> None:
    class PesquisaSession(FakeSession):
        async def refresh(self, obj) -> None:
            await super().refresh(obj)
            if isinstance(obj, PesquisaMarca):
                obj.id = "pesquisa-fluxo"

    async def nenhum(*_args, **_kwargs):
        return None

    async def fase_alterada(*_args, **_kwargs):
        return True

    monkeypatch.setattr("app.api.pesquisas.validar_limite_pesquisas", nenhum)
    monkeypatch.setattr("app.api.pesquisas.obter_ou_criar_empresa", nenhum)
    monkeypatch.setattr("app.api.pesquisas.buscar_lead_ativo_por_email", nenhum)
    monkeypatch.setattr("app.api.pesquisas.detectar_pesquisa_duplicada", nenhum)
    monkeypatch.setattr("app.api.pesquisas.avancar_fase_lead", fase_alterada)
    monkeypatch.setattr("app.api.pesquisas.enviar_alerta_nova_pesquisa", nenhum)

    session = PesquisaSession()
    app.dependency_overrides[get_session] = _session_override(session)
    try:
        resposta = TestClient(app).post(
            "/v1/pesquisas-marca",
            json={
                "nome": "Contato Teste",
                "empresa": "Empresa Teste",
                "email_corporativo": "contato@empresa.test",
                "telefone": "16999999999",
                "marca": "Marca Teste",
                "atividade": "Serviços de tecnologia",
                "aceite_privacidade": True,
                "aceite_marketing": False,
                "website": "",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 201, resposta.text
    dados = resposta.json()
    assert dados["relatorio_url"] == "/relatorios/pesquisa-fluxo"
    assert "chave_integracao" not in dados
    validar_token_relatorio(dados["relatorio_token"], "pesquisa-fluxo", 1)

    web = Path("app/web/static")
    scripts_publicos = "\n".join(
        (web / nome).read_text(encoding="utf-8")
        for nome in ("app.js", "relatorio.js", "tenant-branding.js", "admin-analise.js")
    )
    assert "chave_integracao" not in scripts_publicos
    assert "X-Integration-Key" not in scripts_publicos
    assert "#token=" in scripts_publicos
    assert '"X-Report-Token"' in scripts_publicos

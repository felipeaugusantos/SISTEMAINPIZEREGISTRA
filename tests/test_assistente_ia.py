"""Assistente interno de CRM (chat, decisão do usuário em 11/09/2026):
consulta dados de leads em linguagem natural via function-calling do
Gemini. Só lê dados (nenhuma ferramenta de escrita), sempre restrito à
organização do usuário logado.

Isolados e determinísticos: usam FakeSession (tests/conftest.py) e
monkeypatch na chamada real ao Gemini -- nunca tocam a API de verdade.
"""

import asyncio

import httpx
from fastapi.testclient import TestClient

import app.api.assistente_ia as modulo
from app.api.assistente_ia import (
    PerguntaInput,
    _tool_buscar_leads,
    _tool_contar_leads_por_status,
    _tool_leads_prioritarios,
    perguntar,
)
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Lead, StatusLead
from app.settings import get_settings
from tests.conftest import FakeResult, FakeSession, auth_override, sessao_override, usuario_teste


def _lead(**kwargs: object) -> Lead:
    base: dict = dict(
        id=1,
        organizacao_id=1,
        nome="Fulano de Tal",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        origem="site",
        status=StatusLead.NOVO,
    )
    base.update(kwargs)
    return Lead(**base)


def test_tool_contar_leads_por_status_recusa_sem_permissao_leads() -> None:
    """Achado do usuário (11/09/2026): o assistente ficou disponível para
    qualquer usuário autenticado, não só quem tem leads.view -- mas isso não
    deve abrir uma brecha de permissão. Cada ferramenta recusa por conta
    própria quando falta leads.view, sem tocar o banco."""
    session = FakeSession([])
    usuario = usuario_teste(perfil="financeiro", permissoes=frozenset({"finance.view"}))

    resultado = asyncio.run(_tool_contar_leads_por_status(session, usuario, {}))

    assert "erro" in resultado
    assert session.executados == []


def test_tool_buscar_leads_recusa_sem_permissao_leads() -> None:
    session = FakeSession([])
    usuario = usuario_teste(perfil="financeiro", permissoes=frozenset({"finance.view"}))

    resultado = asyncio.run(_tool_buscar_leads(session, usuario, {}))

    assert "erro" in resultado
    assert session.executados == []


def test_tool_leads_prioritarios_recusa_sem_permissao_leads() -> None:
    session = FakeSession([])
    usuario = usuario_teste(perfil="financeiro", permissoes=frozenset({"finance.view"}))

    resultado = asyncio.run(_tool_leads_prioritarios(session, usuario, {"criterio": "atrasadas"}))

    assert "erro" in resultado
    assert session.executados == []


def test_tool_contar_leads_por_status_agrupa_corretamente() -> None:
    session = FakeSession([FakeResult(itens=[(StatusLead.NOVO, 5), (StatusLead.QUALIFICADO, 2)])])
    usuario = usuario_teste()

    resultado = asyncio.run(_tool_contar_leads_por_status(session, usuario, {}))

    assert resultado == {"contagem_por_status": {"novo": 5, "qualificado": 2}}


def test_tool_buscar_leads_mascara_pii_sem_permissao() -> None:
    session = FakeSession([FakeResult(itens=[_lead()])])
    usuario = usuario_teste(perfil="operador", permissoes=frozenset({"leads.view"}))

    resultado = asyncio.run(_tool_buscar_leads(session, usuario, {"busca": "ACME"}))

    assert resultado["total_encontrado"] == 1
    assert "email" not in resultado["leads"][0]
    assert "telefone" not in resultado["leads"][0]


def test_tool_buscar_leads_expoe_pii_com_permissao() -> None:
    session = FakeSession([FakeResult(itens=[_lead()])])
    usuario = usuario_teste(perfil="comercial", permissoes=frozenset({"leads.view", "leads.pii.view"}))

    resultado = asyncio.run(_tool_buscar_leads(session, usuario, {}))

    assert resultado["leads"][0]["email"] == "fulano@example.com"


def test_tool_buscar_leads_limita_ao_teto_maximo() -> None:
    session = FakeSession([FakeResult(itens=[])])
    usuario = usuario_teste()

    resultado = asyncio.run(_tool_buscar_leads(session, usuario, {"limite": 999}))

    assert resultado["total_encontrado"] == 0


def test_tool_leads_prioritarios_criterio_invalido_devolve_erro() -> None:
    session = FakeSession([])
    usuario = usuario_teste()

    resultado = asyncio.run(_tool_leads_prioritarios(session, usuario, {"criterio": "invalido"}))

    assert "erro" in resultado


def test_tool_leads_prioritarios_atrasadas() -> None:
    session = FakeSession([FakeResult(itens=[_lead()])])
    usuario = usuario_teste()

    resultado = asyncio.run(_tool_leads_prioritarios(session, usuario, {"criterio": "atrasadas"}))

    assert resultado["total_encontrado"] == 1
    assert resultado["criterio"] == "atrasadas"


def test_perguntar_executa_tool_e_retorna_resposta_final(monkeypatch) -> None:
    chamadas = []

    async def _fake_chamar(contents, *, http_client):
        chamadas.append(contents)
        if len(chamadas) == 1:
            return {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"functionCall": {"name": "contar_leads_por_status", "args": {}}}],
                        }
                    }
                ]
            }
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": "Você tem 5 leads novos."}]}}]}

    monkeypatch.setattr(modulo, "_chamar_gemini_bruto", _fake_chamar)
    session = FakeSession([FakeResult(itens=[(StatusLead.NOVO, 5)])])
    usuario = usuario_teste()

    resultado = asyncio.run(perguntar(session, usuario, "quantos leads novos?", []))

    assert resultado == "Você tem 5 leads novos."
    assert len(chamadas) == 2


def test_perguntar_limite_de_rodadas_de_tool_nao_trava(monkeypatch) -> None:
    async def _fake_chamar(contents, *, http_client):
        return {
            "candidates": [
                {"content": {"role": "model", "parts": [{"functionCall": {"name": "contar_leads_por_status", "args": {}}}]}}
            ]
        }

    monkeypatch.setattr(modulo, "_chamar_gemini_bruto", _fake_chamar)
    session = FakeSession([FakeResult(itens=[]) for _ in range(modulo.MAX_RODADAS_TOOL)])
    usuario = usuario_teste()

    resultado = asyncio.run(perguntar(session, usuario, "pergunta qualquer", []))

    assert "consultas demais" in resultado


def test_perguntar_endpoint_recusado_quando_desativado() -> None:
    settings = get_settings()
    original = settings.assistente_crm_enabled
    settings.assistente_crm_enabled = False
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = sessao_override()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/assistente/perguntar",
            json={"pergunta": "oi", "historico": []},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        settings.assistente_crm_enabled = original

    assert resposta.status_code == 503


def test_perguntar_endpoint_recusado_sem_chave_configurada() -> None:
    settings = get_settings()
    original_enabled = settings.assistente_crm_enabled
    original_key = settings.gemini_api_key
    settings.assistente_crm_enabled = True
    settings.gemini_api_key = ""
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = sessao_override()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/assistente/perguntar",
            json={"pergunta": "oi", "historico": []},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        settings.assistente_crm_enabled = original_enabled
        settings.gemini_api_key = original_key

    assert resposta.status_code == 503


def test_perguntar_endpoint_aceita_usuario_sem_permissao_de_leads(monkeypatch) -> None:
    """Achado do usuário (11/09/2026): o endpoint não exige mais leads.view
    -- qualquer usuário autenticado pode conversar com o assistente. Quem
    não tem leads.view continua sem ver dados de lead (a ferramenta recusa),
    mas a conversa em si não é bloqueada com 403."""

    async def _fake_chamar(contents, *, http_client):
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": "Não posso ver esses dados."}]}}]}

    monkeypatch.setattr(modulo, "_chamar_gemini_bruto", _fake_chamar)
    settings = get_settings()
    original_enabled = settings.assistente_crm_enabled
    original_key = settings.gemini_api_key
    settings.assistente_crm_enabled = True
    settings.gemini_api_key = "chave-de-teste"
    usuario = usuario_teste(perfil="financeiro", permissoes=frozenset({"finance.view"}))
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = sessao_override()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/assistente/perguntar",
            json={"pergunta": "quantos leads temos?", "historico": []},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        settings.assistente_crm_enabled = original_enabled
        settings.gemini_api_key = original_key

    assert resposta.status_code == 200
    assert resposta.json()["resposta"] == "Não posso ver esses dados."


def test_perguntar_recorta_historico_longo_para_o_limite() -> None:
    """PerguntaInput aceita histórico de qualquer tamanho; perguntar() é
    quem recorta para as últimas MAX_MENSAGENS_HISTORICO entradas antes de
    montar o prompt (evita prompt gigante em conversas longas)."""

    async def _fake_chamar(contents, *, http_client):
        # -1 porque a última entrada de "contents" é a pergunta atual, não
        # faz parte do histórico recortado.
        assert len(contents) - 1 == modulo.MAX_MENSAGENS_HISTORICO
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]}

    monkeypatch_alvo = modulo._chamar_gemini_bruto
    modulo._chamar_gemini_bruto = _fake_chamar
    try:
        historico_grande = [{"role": "user", "texto": f"mensagem {i}"} for i in range(20)]
        entrada = PerguntaInput(pergunta="teste", historico=historico_grande)
        session = FakeSession([])
        usuario = usuario_teste()
        resultado = asyncio.run(perguntar(session, usuario, entrada.pergunta, entrada.historico))
    finally:
        modulo._chamar_gemini_bruto = monkeypatch_alvo

    assert resultado == "ok"


def test_perguntar_endpoint_traduz_429_do_gemini_em_mensagem_clara(monkeypatch) -> None:
    """Achado ao vivo em produção (11/09/2026): a cota gratuita do Gemini
    estourou (429) durante um teste real -- o endpoint devolvia um 502
    genérico. Agora reconhece 429 especificamente e devolve uma mensagem que
    explica o motivo em vez de "não foi possível consultar"."""

    async def _fake_chamar(contents, *, http_client):
        resposta = httpx.Response(429, request=httpx.Request("POST", "https://example.com"))
        raise httpx.HTTPStatusError("429", request=resposta.request, response=resposta)

    monkeypatch.setattr(modulo, "_chamar_gemini_bruto", _fake_chamar)
    settings = get_settings()
    original_enabled = settings.assistente_crm_enabled
    original_key = settings.gemini_api_key
    settings.assistente_crm_enabled = True
    settings.gemini_api_key = "chave-de-teste"
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = sessao_override()
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/assistente/perguntar",
            json={"pergunta": "oi", "historico": []},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        settings.assistente_crm_enabled = original_enabled
        settings.gemini_api_key = original_key

    assert resposta.status_code == 429
    assert "limite" in resposta.json()["detail"].lower()


def test_chamar_gemini_bruto_cai_para_fallback_quando_principal_devolve_429() -> None:
    """Mesmo fallback automático de app.ia_sombra.chamar_gemini (achado do
    usuário, 11/09/2026), aplicado à chamada com tools do assistente."""
    settings = get_settings()
    original_key = settings.gemini_api_key
    original_intervalo = settings.gemini_intervalo_minimo_segundos
    settings.gemini_api_key = "chave-teste"
    settings.gemini_intervalo_minimo_segundos = 0.0

    requisicao_fake = httpx.Request("POST", "https://generativelanguage.googleapis.com/fake")
    urls_chamadas = []

    class _ClienteFake:
        def __init__(self) -> None:
            self._respostas = [
                httpx.Response(429, json={"error": "quota"}, request=requisicao_fake),
                httpx.Response(
                    200,
                    json={"candidates": [{"content": {"role": "model", "parts": [{"text": "ok pelo fallback"}]}}]},
                    request=requisicao_fake,
                ),
            ]

        async def post(self, url, *, json, headers):
            urls_chamadas.append(url)
            return self._respostas.pop(0)

    try:
        dados = asyncio.run(modulo._chamar_gemini_bruto([{"role": "user", "parts": [{"text": "oi"}]}], http_client=_ClienteFake()))
    finally:
        settings.gemini_api_key = original_key
        settings.gemini_intervalo_minimo_segundos = original_intervalo

    assert dados["candidates"][0]["content"]["parts"][0]["text"] == "ok pelo fallback"
    assert len(urls_chamadas) == 2
    assert settings.gemini_modelo in urls_chamadas[0]
    assert settings.gemini_modelo_fallback in urls_chamadas[1]

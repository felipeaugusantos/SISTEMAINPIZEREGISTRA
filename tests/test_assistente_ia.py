"""Assistente interno de CRM (chat, decisão do usuário em 11/09/2026):
consulta dados de leads em linguagem natural via function-calling do
Gemini. Só lê dados (nenhuma ferramenta de escrita), sempre restrito à
organização do usuário logado.

Isolados e determinísticos: usam FakeSession (tests/conftest.py) e
monkeypatch na chamada real ao Gemini -- nunca tocam a API de verdade.
"""

import asyncio

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

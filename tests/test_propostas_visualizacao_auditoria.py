"""Achado 18.7 da auditoria fina de Propostas comerciais (29/09/2026).

- Abrir o link público (GET) marcava a proposta como "visualizada" --
  antivírus e pré-visualizadores de e-mail abrem os links sozinhos e
  inflavam a métrica. A marcação passou a ser um POST disparado pela página
  no navegador (static/proposta-publica.js).
- Gerar link e enviar proposta não deixavam rastro na auditoria.
- Encontrado no caminho: as páginas públicas usavam <style> embutido e
  style="..." em atributo, bloqueados pelo CSP "style-src 'self'"
  (app/observability.py) -- apareciam sem formatação. Estilos foram para
  static/proposta-publica.css.

Isolados e determinísticos: FakeSession (tests/conftest.py), sem banco real.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import app.api.leads_propostas as leads_propostas
from app.api.leads_propostas import _pagina_codigo, marcar_proposta_visualizada, visualizar_proposta_publica
from app.models import EventoAuditoria, Lead, Organizacao, PropostaComercial
from tests.conftest import FakeResult, FakeSession, usuario_teste
from tests.test_phase4_proposals import _request_post

ESTATICOS = Path(__file__).resolve().parents[1] / "app" / "web" / "static"


def _proposta(**kwargs: object) -> PropostaComercial:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "lead_id": 9,
        "numero": "PROP-2026-000010",
        "versao": 1,
        "escopo": "Registro de marca no INPI",
        "status": "enviada",
        "honorarios": 1500,
        "taxa_gru": 415,
        "public_token_hash": "hash-qualquer",
        "public_token_expira_em": datetime.now(UTC) + timedelta(days=3),
        "dados": {},
    }
    base.update(kwargs)
    return PropostaComercial(**base)


def _organizacao() -> Organizacao:
    return Organizacao(id=1, nome="Escritório Teste", slug="escritorio-teste")


# --- visualização ----------------------------------------------------------------


def test_abrir_o_link_nao_marca_a_proposta_como_visualizada() -> None:
    proposta = _proposta(status="enviada")
    session = FakeSession([FakeResult(scalar=proposta)], objetos_get=[_organizacao()])

    resposta = asyncio.run(visualizar_proposta_publica("token-abc", session))

    assert proposta.status == "enviada"
    assert session.commits == 0
    corpo = resposta.body.decode("utf-8")
    assert "data-marcar-visualizada='/propostas/token-abc/visualizada'" in corpo
    assert "/static/proposta-publica.js" in corpo


@pytest.mark.parametrize(("status", "esperado"), [("enviada", "visualizada"), ("aceita", "aceita"), ("cancelada", "cancelada")])
def test_post_da_pagina_so_avanca_enviada_para_visualizada(status: str, esperado: str) -> None:
    proposta = _proposta(status=status)
    session = FakeSession([FakeResult(scalar=proposta)])

    resposta = asyncio.run(marcar_proposta_visualizada("token-abc", session))

    assert resposta.status_code == 204
    assert proposta.status == esperado


def test_post_da_pagina_trava_a_linha_antes_de_rechecar_o_status() -> None:
    """Revisão do Codex no PR #150: sem a trava, um fetch atrasado
    concorrendo com cancelamento/aceite podia sobrescrever o status."""
    from sqlalchemy.dialects import postgresql

    session = FakeSession([FakeResult(scalar=_proposta(status="enviada"))])

    asyncio.run(marcar_proposta_visualizada("token-abc", session))

    assert "FOR UPDATE" in str(session.executados[0].compile(dialect=postgresql.dialect()))


def test_post_com_token_invalido_responde_204_sem_alterar_nada() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    resposta = asyncio.run(marcar_proposta_visualizada("token-inexistente", session))

    assert resposta.status_code == 204
    assert session.commits == 0


# --- CSP --------------------------------------------------------------------------


def test_paginas_publicas_nao_usam_estilo_embutido_bloqueado_pelo_csp() -> None:
    session = FakeSession([FakeResult(scalar=_proposta())], objetos_get=[_organizacao()])
    pagina_proposta = asyncio.run(visualizar_proposta_publica("token-abc", session)).body.decode("utf-8")
    pagina_codigo = _pagina_codigo("token-abc", aviso="Código incorreto.").body.decode("utf-8")

    for pagina in (pagina_proposta, pagina_codigo):
        assert "<style" not in pagina
        assert "style=" not in pagina
        assert "/static/proposta-publica.css" in pagina


def test_arquivos_estaticos_da_pagina_publica_existem() -> None:
    css = (ESTATICOS / "proposta-publica.css").read_text(encoding="utf-8")
    script = (ESTATICOS / "proposta-publica.js").read_text(encoding="utf-8")
    assert ".button-secundario" in css
    assert "data-marcar-visualizada" in script or "marcarVisualizada" in script
    assert 'method: "POST"' in script


# --- auditoria --------------------------------------------------------------------


def test_gerar_link_registra_auditoria_sem_o_token() -> None:
    proposta = _proposta(status="rascunho")
    session = FakeSession([FakeResult(scalar=proposta)])

    resultado = asyncio.run(
        leads_propostas.criar_link_proposta(1, _request_post("/propostas/1/link"), session, usuario_teste())
    )

    eventos = [obj for obj in session.adicionados if isinstance(obj, EventoAuditoria)]
    assert [evento.acao for evento in eventos] == ["gerar_link_proposta"]
    token = resultado["link"].rsplit("/", 1)[-1]
    assert token not in str(eventos[0].detalhes)


def test_enviar_proposta_registra_auditoria_sem_o_email(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _enviar_fake(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(leads_propostas, "enviar_proposta_email", _enviar_fake)
    monkeypatch.setattr(leads_propostas, "gerar_pdf_proposta", lambda _dados: b"%PDF-teste")
    monkeypatch.setattr(leads_propostas, "configuracao_clicksign", lambda _org=None: {"enabled": False})
    lead = Lead(id=9, organizacao_id=1, nome="Cliente", email="cliente@example.com", telefone="", marca="ACME")
    session = FakeSession(
        [FakeResult(scalar=_proposta(status="rascunho")), FakeResult(scalar=lead)], objetos_get=[_organizacao()]
    )

    asyncio.run(leads_propostas.enviar_link_proposta(1, _request_post("/propostas/1/enviar"), session, usuario_teste()))

    eventos = [obj for obj in session.adicionados if isinstance(obj, EventoAuditoria)]
    assert [evento.acao for evento in eventos] == ["enviar_proposta"]
    assert eventos[0].sucesso is True
    assert "cliente@example.com" not in str(eventos[0].detalhes)


def test_falha_no_envio_registra_tentativa_malsucedida(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão do Codex no PR #150: o sucesso era gravado antes do envio
    externo -- uma falha no e-mail deixava um "enviado" que nunca chegou."""

    async def _falhar(*_args, **_kwargs) -> None:
        raise RuntimeError("SMTP indisponível")

    monkeypatch.setattr(leads_propostas, "enviar_proposta_email", _falhar)
    monkeypatch.setattr(leads_propostas, "gerar_pdf_proposta", lambda _dados: b"%PDF-teste")
    monkeypatch.setattr(leads_propostas, "configuracao_clicksign", lambda _org=None: {"enabled": False})
    lead = Lead(id=9, organizacao_id=1, nome="Cliente", email="cliente@example.com", telefone="", marca="ACME")
    session = FakeSession(
        [FakeResult(scalar=_proposta(status="rascunho")), FakeResult(scalar=lead)], objetos_get=[_organizacao()]
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            leads_propostas.enviar_link_proposta(1, _request_post("/propostas/1/enviar"), session, usuario_teste())
        )

    eventos = [obj for obj in session.adicionados if isinstance(obj, EventoAuditoria)]
    assert len(eventos) == 1
    assert eventos[0].sucesso is False
    assert eventos[0].detalhes["erro"] == "RuntimeError"

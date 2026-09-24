import asyncio
from datetime import date

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.leads_guias import (
    GuiaInpiInput,
    GuiaStatusInput,
    atualizar_guia_inpi,
    criar_guia_inpi,
    listar_guias_inpi,
    remover_guia_inpi,
)
from app.models import EventoAuditoria, GuiaInpi, Lead
from tests.conftest import FakeResult, FakeSession, usuario_teste

# --- Achado da Fase 15.1 (auditoria fina de Leads, 23/09/2026): as 3
# mutações de GuiaInpi (guia de pagamento ao INPI, entidade financeira
# sensível) não deixavam nenhum rastro de auditoria -- diferente do resto
# do módulo de leads, que audita quase toda mutação relevante. E não
# havia nenhum teste pra este módulo inteiro. ---


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/leads/1/guias",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_listar_guias_inpi_lead_inexistente_devolve_404() -> None:
    usuario = usuario_teste()
    session = FakeSession([FakeResult(scalar=None)])
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(listar_guias_inpi(1, session, usuario))
    assert exc_info.value.status_code == 404


def test_listar_guias_inpi_devolve_guias_e_sugestoes() -> None:
    usuario = usuario_teste()
    lead = Lead(id=1, organizacao_id=usuario.organizacao_id, fase="protocolo_inpi")
    guia = GuiaInpi(id=9, organizacao_id=usuario.organizacao_id, lead_id=1, descricao="Depósito de pedido")
    session = FakeSession([FakeResult(scalar=lead), FakeResult(itens=[guia]), FakeResult(itens=[])])

    resultado = asyncio.run(listar_guias_inpi(1, session, usuario))

    assert resultado["fase"] == "protocolo_inpi"
    assert resultado["guias"][0]["id"] == 9


def test_criar_guia_inpi_registra_evento_auditoria() -> None:
    usuario = usuario_teste()
    session = FakeSession([FakeResult(scalar=1)])
    dados = GuiaInpiInput(descricao="Depósito de pedido de marca")

    resultado = asyncio.run(criar_guia_inpi(1, dados, _request(), session, usuario))

    assert resultado["id"] is not None
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "criar_guia"
    assert evento.recurso == f"lead:1:guia:{resultado['id']}"
    assert evento.detalhes == {"descricao": "Depósito de pedido de marca"}
    # Achado P2 do Codex no PR #132: o endpoint responde 201, o rastro de
    # auditoria precisa refletir isso em vez do padrão 200 de _auditar.
    assert evento.status_http == 201
    assert session.commits == 1


def test_atualizar_guia_inpi_inexistente_devolve_404() -> None:
    usuario = usuario_teste()
    session = FakeSession([FakeResult(scalar=None)])
    dados = GuiaStatusInput(status="paga")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(atualizar_guia_inpi(9, dados, _request(), session, usuario))
    assert exc_info.value.status_code == 404
    assert session.adicionados == []


def test_atualizar_guia_inpi_registra_evento_auditoria() -> None:
    usuario = usuario_teste()
    guia = GuiaInpi(id=9, organizacao_id=usuario.organizacao_id, lead_id=1, descricao="Depósito", status="pendente")
    session = FakeSession([FakeResult(scalar=guia)])
    dados = GuiaStatusInput(status="paga", pago_em=date(2026, 9, 23))

    resultado = asyncio.run(atualizar_guia_inpi(9, dados, _request(), session, usuario))

    assert resultado["ok"] is True
    assert guia.status == "paga"
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "atualizar_guia"
    assert evento.recurso == "lead:1:guia:9"
    assert evento.detalhes == {"status": "paga", "pago_em": "2026-09-23"}
    assert session.commits == 1


def test_remover_guia_inpi_inexistente_devolve_404() -> None:
    usuario = usuario_teste()
    session = FakeSession([FakeResult(scalar=None)])
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(remover_guia_inpi(9, _request(), session, usuario))
    assert exc_info.value.status_code == 404
    assert session.adicionados == []


def test_remover_guia_inpi_registra_evento_auditoria() -> None:
    usuario = usuario_teste()
    guia = GuiaInpi(id=9, organizacao_id=usuario.organizacao_id, lead_id=1, descricao="Depósito")
    session = FakeSession([FakeResult(scalar=guia)])

    asyncio.run(remover_guia_inpi(9, _request(), session, usuario))

    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "remover_guia"
    assert evento.recurso == "lead:1:guia:9"
    assert evento.status_http == 204
    assert guia in session.deletados
    assert session.commits == 1

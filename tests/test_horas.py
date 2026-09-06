import asyncio
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.api.horas import (
    ApontamentoInput,
    criar_apontamento,
    editar_apontamento,
    excluir_apontamento,
    listar_apontamentos,
)
from app.models import ApontamentoHoras
from tests.conftest import FakeResult, FakeSession, usuario_teste

# --- Achado FASE-A da auditoria do CRM (05/09/2026): apontamento de horas fatuaveis. ---


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/horas",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
        }
    )


def _apontamento(**kwargs: object) -> ApontamentoHoras:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "usuario_id": 1,
        "lead_id": 9,
        "processo_monitorado_id": None,
        "data": date(2026, 9, 5),
        "horas": Decimal("2.50"),
        "descricao": "Análise de viabilidade",
        "faturavel": True,
        "usuario": None,
    }
    base.update(kwargs)
    return ApontamentoHoras(**base)


def test_apontamento_input_exige_lead_ou_processo() -> None:
    with pytest.raises(ValidationError):
        ApontamentoInput(data=date(2026, 9, 5), horas=Decimal("1"), descricao="teste sem vinculo")


def test_apontamento_input_aceita_so_processo() -> None:
    dados = ApontamentoInput(
        processo_monitorado_id=5, data=date(2026, 9, 5), horas=Decimal("1"), descricao="teste com processo"
    )
    assert dados.lead_id is None
    assert dados.processo_monitorado_id == 5


def test_criar_apontamento_vincula_ao_lead_e_audita() -> None:
    session = FakeSession([FakeResult(scalar=9)])  # valida que o lead existe
    dados = ApontamentoInput(lead_id=9, data=date(2026, 9, 5), horas=Decimal("2.5"), descricao="Análise de marca")
    resultado = asyncio.run(criar_apontamento(dados, _request(), session, usuario_teste()))
    assert resultado["horas"] == "2.50"
    assert resultado["lead_id"] == 9
    assert session.commits == 1
    assert len(session.adicionados) == 2  # ApontamentoHoras + EventoAuditoria


def test_criar_apontamento_lead_inexistente_retorna_404() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    dados = ApontamentoInput(lead_id=999, data=date(2026, 9, 5), horas=Decimal("1"), descricao="teste")
    with pytest.raises(HTTPException) as erro:
        asyncio.run(criar_apontamento(dados, _request(), session, usuario_teste()))
    assert erro.value.status_code == 404


def test_listar_apontamentos_soma_totais_faturaveis_e_nao_faturaveis() -> None:
    itens = [
        _apontamento(id=1, horas=Decimal("2.00"), faturavel=True),
        _apontamento(id=2, horas=Decimal("1.50"), faturavel=False),
    ]
    session = FakeSession([FakeResult(itens=itens)])
    resultado = asyncio.run(listar_apontamentos(session, usuario_teste(), lead_id=9))
    assert resultado["total_horas"] == "3.50"
    assert resultado["total_horas_faturaveis"] == "2.00"
    assert len(resultado["itens"]) == 2


def test_editar_apontamento_de_outro_usuario_exige_admin() -> None:
    item = _apontamento(usuario_id=42)
    session = FakeSession([FakeResult(scalar=item)])
    dados = ApontamentoInput(lead_id=9, data=date(2026, 9, 5), horas=Decimal("1"), descricao="edição")
    with pytest.raises(HTTPException) as erro:
        asyncio.run(editar_apontamento(1, dados, _request(), session, usuario_teste(perfil="operador")))
    assert erro.value.status_code == 403


def test_administrador_pode_excluir_apontamento_de_outro_usuario() -> None:
    item = _apontamento(usuario_id=42)
    session = FakeSession([FakeResult(scalar=item)])
    asyncio.run(excluir_apontamento(1, _request(), session, usuario_teste(perfil="administrador")))
    assert session.deletados == [item]
    assert session.commits == 1

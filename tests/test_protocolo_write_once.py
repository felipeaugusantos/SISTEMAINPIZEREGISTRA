"""Achado 9 da auditoria de filas (07/10/2026): re-salvar o protocolo de uma
proposta alterava a data (e podia apagar número/comprovante). O protocolo passa
a ser write-once -- só o motivo do atraso e o responsável mudam depois."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.leads_propostas import PropostaProtocoloInput, registrar_protocolo_proposta
from app.models import PropostaComercial, UsuarioOperacoes
from tests.conftest import FakeResult, FakeSession

DATA_ORIGINAL = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _request() -> Request:
    return Request({"type": "http", "method": "PATCH", "path": "/", "headers": [], "client": ("10.0.0.1", 1)})


def _usuario() -> SimpleNamespace:
    return SimpleNamespace(id=1, organizacao_id=1, ator="operador@teste", nome="Operador", perfil="administrador")


def _proposta_protocolada() -> PropostaComercial:
    return PropostaComercial(
        id=1,
        organizacao_id=1,
        lead_id=None,
        status="aceita",
        pagamento_status="confirmado",
        protocolo_numero="BR112026000001",
        protocolo_em=DATA_ORIGINAL,
        protocolo_comprovante_id=99,
    )


def _sessao(proposta: PropostaComercial) -> FakeSession:
    responsavel = UsuarioOperacoes(id=7, organizacao_id=1, nome="Resp", usuario="resp", email="r@t.local", perfil="administrador")
    sessao = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=responsavel)])
    sessao.info = {"organizacao_id": 1}
    return sessao


def test_resalvar_protocolo_preserva_data_numero_e_comprovante() -> None:
    proposta = _proposta_protocolada()
    dados = PropostaProtocoloInput(responsavel_protocolo_id=7, motivo_atraso="documentação revisada")

    asyncio.run(registrar_protocolo_proposta(1, dados, _request(), _sessao(proposta), _usuario()))

    assert proposta.protocolo_em == DATA_ORIGINAL  # data original preservada
    assert proposta.protocolo_numero == "BR112026000001"  # número preservado
    assert proposta.protocolo_comprovante_id == 99  # comprovante preservado
    assert proposta.protocolo_motivo_atraso == "documentação revisada"  # só o motivo muda


def test_resalvar_protocolo_com_numero_diferente_e_rejeitado() -> None:
    proposta = _proposta_protocolada()
    dados = PropostaProtocoloInput(responsavel_protocolo_id=7, protocolo_numero="BR999NOVO")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(registrar_protocolo_proposta(1, dados, _request(), _sessao(proposta), _usuario()))

    assert exc.value.status_code == 422
    assert proposta.protocolo_numero == "BR112026000001"  # inalterado

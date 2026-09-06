import asyncio

import pytest
from pydantic import ValidationError

from app.api.leads import ContatoInput
from app.crm import normalizar_empresa, verificar_conflito_interesse
from tests.conftest import FakeResult, FakeSession


def test_normaliza_empresa_para_evitar_cadastros_duplicados() -> None:
    assert normalizar_empresa("  Zé   Registra LTDA  ") == "ze registra ltda"
    assert normalizar_empresa("ZE REGISTRA LTDA") == "ze registra ltda"


# --- Achado FASE-A da auditoria do CRM (05/09/2026): checagem nao bloqueante
# de conflito de interesse ao cadastrar/vincular um novo cliente/processo. ---


def test_conflito_interesse_detecta_titular_de_outro_cliente() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[("Acme Comercio Ltda", 7, "Cliente Existente", 100)]),
            FakeResult(itens=[]),
        ]
    )
    achados = asyncio.run(
        verificar_conflito_interesse(session, organizacao_id=1, nomes=["Acme Comercio Ltda"], empresa_id_atual=None)
    )
    assert len(achados) == 1
    assert achados[0]["tipo"] == "titular_outro_cliente"
    assert achados[0]["empresa_nome"] == "Cliente Existente"


def test_conflito_interesse_ignora_mesma_empresa() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[("Acme Comercio Ltda", 7, "Cliente Existente", 100)]),
            FakeResult(itens=[]),
        ]
    )
    achados = asyncio.run(
        verificar_conflito_interesse(session, organizacao_id=1, nomes=["Acme Comercio Ltda"], empresa_id_atual=7)
    )
    assert achados == []


def test_conflito_interesse_detecta_empresa_ja_cliente() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[]),
            FakeResult(itens=[(3, "Beta Industrias", "beta industrias")]),
        ]
    )
    achados = asyncio.run(
        verificar_conflito_interesse(session, organizacao_id=1, nomes=["Beta Indústrias"], empresa_id_atual=None)
    )
    assert len(achados) == 1
    assert achados[0]["tipo"] == "empresa_ja_cliente"


def test_conflito_interesse_sem_nomes_nao_consulta_banco() -> None:
    session = FakeSession([])
    achados = asyncio.run(verificar_conflito_interesse(session, organizacao_id=1, nomes=[None, "  "]))
    assert achados == []


def test_contato_exige_pesquisa_relacionada() -> None:
    with pytest.raises(ValidationError):
        ContatoInput(canal="email", resultado="Proposta enviada")


def test_contato_aceita_pesquisa_uuid() -> None:
    dados = ContatoInput(
        canal="whatsapp",
        resultado="Cliente respondeu",
        pesquisa_id="12345678-1234-1234-1234-123456789abc",
    )

    assert dados.pesquisa_id == "12345678-1234-1234-1234-123456789abc"

import pytest
from pydantic import ValidationError

from app.api.leads import ContatoInput
from app.crm import normalizar_empresa


def test_normaliza_empresa_para_evitar_cadastros_duplicados() -> None:
    assert normalizar_empresa("  Zé   Registra LTDA  ") == "ze registra ltda"
    assert normalizar_empresa("ZE REGISTRA LTDA") == "ze registra ltda"


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

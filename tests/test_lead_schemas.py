import pytest
from pydantic import ValidationError

from app.schemas import LeadCreate


def test_valid_lead() -> None:
    lead = LeadCreate(
        nome="Enzo Silva",
        email="enzo@example.com",
        telefone="(11) 99999-9999",
        marca="Minha Marca",
        processo_numero=" 123456789 ",
        origem="processo",
        aceite_privacidade=True,
    )

    assert lead.email == "enzo@example.com"
    assert lead.processo_numero == "123456789"


def test_lead_requires_valid_phone_and_consent() -> None:
    with pytest.raises(ValidationError):
        LeadCreate(
            nome="Enzo Silva",
            email="enzo@example.com",
            telefone="123",
            marca="Minha Marca",
            aceite_privacidade=False,
        )

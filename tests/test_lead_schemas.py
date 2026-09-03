import pytest
from pydantic import ValidationError

from app.schemas import LeadCreate, LeadStatusUpdate


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


def test_atualizacao_lead_normaliza_cpf_cnpj() -> None:
    # 111.444.777-35 e 11.222.333/0001-81 são os CPF/CNPJ de teste padrão da
    # indústria (dígito verificador real, sem pertencer a ninguém).
    assert LeadStatusUpdate(documento="111.444.777-35").documento == "11144477735"
    assert LeadStatusUpdate(documento="11.222.333/0001-81").documento == "11222333000181"


# --- Achado P1 da auditoria de Leads (03/09/2026): documento só validava o
# tamanho (11/14 dígitos), sem conferir o dígito verificador -- "11111111111"
# passava como CPF válido. ---


def test_cpf_com_digito_verificador_invalido_e_rejeitado() -> None:
    with pytest.raises(ValidationError, match="CPF inválido"):
        LeadStatusUpdate(documento="111.444.777-36")  # último dígito trocado


def test_cpf_com_todos_digitos_iguais_e_rejeitado() -> None:
    with pytest.raises(ValidationError, match="CPF inválido"):
        LeadStatusUpdate(documento="111.111.111-11")


def test_cnpj_com_digito_verificador_invalido_e_rejeitado() -> None:
    with pytest.raises(ValidationError, match="CNPJ inválido"):
        LeadStatusUpdate(documento="11.222.333/0001-82")  # último dígito trocado


def test_documento_vazio_continua_permitido() -> None:
    assert LeadStatusUpdate(documento=None).documento is None
    assert LeadStatusUpdate(documento="").documento is None

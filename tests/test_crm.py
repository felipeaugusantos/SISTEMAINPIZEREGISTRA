import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.api.leads import ContatoInput
from app.crm import calcular_score_lead, normalizar_empresa, verificar_conflito_interesse
from app.models import Lead
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


# --- Item 30 da auditoria completa do CRM (06/09/2026): score simples de
# lead (fit + engajamento com decaimento por tempo sem interação). Decisão
# do usuário: usar só sinais já capturados pelo sistema (completude de
# contato, ausência de proposta perdida, contato humano, resposta de
# e-mail) -- sem inventar peso de origem/porte/setor. ---


def test_score_lead_completo_e_engajado_recentemente_e_maximo() -> None:
    lead = Lead(
        id=1,
        email="a@a.com",
        telefone="11999999999",
        criado_em=datetime.now(UTC) - timedelta(days=100),
    )
    session = FakeSession(
        [
            FakeResult(scalar=None),  # sem proposta recusada/expirada
            FakeResult(scalar=datetime.now(UTC) - timedelta(days=2)),  # último contato
            FakeResult(scalar=datetime.now(UTC) - timedelta(days=1)),  # última resposta de e-mail
        ]
    )

    resultado = asyncio.run(calcular_score_lead(session, lead))

    assert resultado["fit_base"] == 40
    assert resultado["engajamento_bruto"] == 60
    assert resultado["fator_decaimento"] == 1.0
    assert resultado["score"] == 100


def test_score_lead_incompleto_com_proposta_perdida_e_sem_interacao_e_zero() -> None:
    lead = Lead(
        id=2,
        email="b@b.com",
        telefone=None,
        criado_em=datetime.now(UTC) - timedelta(days=90),
    )
    session = FakeSession(
        [
            FakeResult(scalar=1),  # tem proposta recusada/expirada
            FakeResult(scalar=None),  # nunca teve contato humano
            FakeResult(scalar=None),  # nunca respondeu e-mail
        ]
    )

    resultado = asyncio.run(calcular_score_lead(session, lead))

    assert resultado["fit_base"] == 0
    assert resultado["tem_proposta_perdida"] is True
    assert resultado["dias_sem_interacao"] == 90
    assert resultado["fator_decaimento"] == 0.15
    assert resultado["score"] == 0


def test_score_lead_aplica_decaimento_intermediario_ao_engajamento() -> None:
    lead = Lead(
        id=3,
        email="c@c.com",
        telefone="11988887777",
        criado_em=datetime.now(UTC) - timedelta(days=200),
    )
    session = FakeSession(
        [
            FakeResult(scalar=None),  # sem proposta perdida
            FakeResult(scalar=datetime.now(UTC) - timedelta(days=20)),  # último contato há 20 dias
            FakeResult(scalar=None),  # nunca respondeu e-mail
        ]
    )

    resultado = asyncio.run(calcular_score_lead(session, lead))

    assert resultado["engajamento_bruto"] == 30
    assert resultado["fator_decaimento"] == 0.6
    # fit_base (40, não decai) + round(30 * 0.6) = 40 + 18 = 58
    assert resultado["score"] == 58

    assert dados.pesquisa_id == "12345678-1234-1234-1234-123456789abc"

"""Achados 18.3 e 18.4 da auditoria fina de Propostas comerciais (29/09/2026).

18.3: reenviar o mesmo status (PATCH idempotente) regravava a data de
envio/aceite e o registro do cancelamento com o horário e o motivo da
repetição -- SLA, métricas e histórico perdiam o dado original.

18.4: o texto da visualização da proposta era montado com "\\\\n" no
f-string (barra invertida + "n" literais), e a tela mostrava "\\n" escrito
numa linha só.

Isolados e determinísticos: FakeSession (tests/conftest.py), sem banco real.
"""

import asyncio
from datetime import UTC, datetime

from app.api.leads_propostas import (
    PropostaStatusInput,
    _texto_documento_proposta,
    atualizar_status_proposta,
    documento_proposta,
)
from app.models import Organizacao, PropostaComercial
from tests.conftest import FakeResult, FakeSession, usuario_teste
from tests.test_phase4_proposals import _request_patch

_DATA_ORIGINAL = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _proposta(**kwargs: object) -> PropostaComercial:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "lead_id": 9,
        "numero": "PROP-2026-000010",
        "versao": 1,
        "escopo": "Registro de marca no INPI",
        "marca": "ACME",
        "classes": "35",
        "dados": {"cliente": "Cliente Teste", "conta_contabil_honorarios_id": 11, "conta_contabil_taxa_gru_id": 12},
    }
    base.update(kwargs)
    return PropostaComercial(**base)


def _organizacao(**kwargs: object) -> Organizacao:
    base: dict = {"id": 1, "nome": "Escritório Teste", "slug": "escritorio-teste"}
    base.update(kwargs)
    return Organizacao(**base)


# --- 18.3 ----------------------------------------------------------------------


def test_reenviar_status_aceita_nao_regrava_a_data_do_aceite() -> None:
    proposta = _proposta(status="aceita", aceito_em=_DATA_ORIGINAL)
    session = FakeSession([FakeResult(scalar=proposta)])

    asyncio.run(
        atualizar_status_proposta(
            1, PropostaStatusInput(status="aceita"), _request_patch("/propostas/1/status"), session, usuario_teste()
        )
    )

    assert proposta.aceito_em == _DATA_ORIGINAL


def test_reenviar_status_enviada_nao_regrava_a_data_de_envio() -> None:
    proposta = _proposta(status="enviada", enviado_em=_DATA_ORIGINAL)
    session = FakeSession([FakeResult(scalar=proposta)])

    asyncio.run(
        atualizar_status_proposta(
            1, PropostaStatusInput(status="enviada"), _request_patch("/propostas/1/status"), session, usuario_teste()
        )
    )

    assert proposta.enviado_em == _DATA_ORIGINAL


def test_reenviar_cancelamento_preserva_o_motivo_original() -> None:
    cancelamento = {"motivo": "Cliente desistiu.", "em": _DATA_ORIGINAL.isoformat(), "por": "Operador"}
    proposta = _proposta(status="cancelada", dados={"cancelamento": cancelamento})
    session = FakeSession([FakeResult(scalar=proposta)])

    asyncio.run(
        atualizar_status_proposta(
            1,
            PropostaStatusInput(status="cancelada", motivo="Outro motivo qualquer."),
            _request_patch("/propostas/1/status"),
            session,
            usuario_teste(),
        )
    )

    assert proposta.dados["cancelamento"] == cancelamento


def test_primeiro_envio_continua_gravando_a_data() -> None:
    proposta = _proposta(status="rascunho")
    session = FakeSession([FakeResult(scalar=proposta)])

    asyncio.run(
        atualizar_status_proposta(
            1, PropostaStatusInput(status="enviada"), _request_patch("/propostas/1/status"), session, usuario_teste()
        )
    )

    assert proposta.enviado_em is not None


# --- 18.4 ----------------------------------------------------------------------


def test_texto_da_visualizacao_usa_quebras_de_linha_reais() -> None:
    texto = _texto_documento_proposta(
        _proposta(), _organizacao(branding={"cnpj": "00.000.000/0001-00", "site": "https://exemplo.test"})
    )

    assert "\\n" not in texto
    linhas = texto.split("\n")
    assert linhas[0] == "PROPOSTA DE REGISTRO DE MARCA"
    assert "Escritório Teste" in linhas
    assert "00.000.000/0001-00" in linhas
    assert "Site: https://exemplo.test" in linhas
    assert "Cliente: Cliente Teste" in linhas
    assert "Marca: ACME" in linhas


def test_texto_da_visualizacao_omite_contatos_vazios() -> None:
    texto = _texto_documento_proposta(_proposta(dados=None), _organizacao(branding=None))

    assert "Site:" not in texto
    assert "Telefone:" not in texto
    assert "E-mail:" not in texto
    assert "Cliente: " in texto


def test_endpoint_do_documento_devolve_o_texto_formatado() -> None:
    session = FakeSession([FakeResult(scalar=_proposta(status="enviada"))], objetos_get=[_organizacao()])

    resultado = asyncio.run(documento_proposta(1, session, usuario_teste()))

    assert "\n" in resultado["texto"]
    assert "\\n" not in resultado["texto"]

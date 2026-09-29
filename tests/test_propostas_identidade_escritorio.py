"""Achado 18.6 da auditoria fina de Propostas comerciais (29/09/2026).

O sistema vai atender outros escritórios (decisão do usuário), mas tudo o
que o cliente recebe numa proposta saía com a marca e os preços da Zé
Registra: nome fixo em _proposta_dict, no PDF (cabeçalho, destaque, texto de
abertura, rodapé) e no assunto do e-mail; honorários/taxa GRU padrão fixos
(1500/415). De quebra:
- o quadro de pagamento do PDF era fixo ("em até 10x no cartão / via Pix"),
  contradizendo a forma de pagamento da proposta (achado 18.5);
- a tela "Modelo de propostas" deixava inserir campos {{...}} nos textos,
  mas o PDF nunca os resolvia.

Isolados e determinísticos: FakeSession (tests/conftest.py), sem banco real.
"""

import asyncio
import inspect
from decimal import Decimal

import pytest

import app.emailing as emailing
from app.api.leads_propostas import _proposta_dict, identidade_organizacao
from app.api.propostas_config import (
    PropostaConfigInput,
    _config,
    salvar_configuracao,
    valores_padrao_proposta,
)
from app.models import Organizacao, PropostaComercial
from app.relatorios import gerar_pdf_proposta, resolver_campos_proposta
from app.settings import Settings
from tests.conftest import FakeSession, usuario_teste


def _organizacao(**kwargs: object) -> Organizacao:
    base: dict = {"id": 2, "nome": "Escritorio Beta Ltda", "slug": "escritorio-beta"}
    base.update(kwargs)
    return Organizacao(**base)


def _proposta() -> PropostaComercial:
    return PropostaComercial(
        id=1,
        lead_id=9,
        numero="PROP-2026-000010",
        versao=2,
        escopo="Registro de marca no INPI",
        marca="ACME",
        classes="35",
        honorarios=Decimal("2000.00"),
        taxa_gru=Decimal("440.00"),
        condicoes_pagamento="Honorários em 3 parcelas mensais, a primeira no aceite da proposta.",
        dados={"cliente": "Maria Cliente", "email": "maria@example.com"},
    )


# --- identidade do escritório ------------------------------------------------------


def test_identidade_usa_o_nome_exibido_e_os_contatos_da_organizacao() -> None:
    org = _organizacao(
        branding={"nome_exibido": "Beta Marcas", "cnpj": "00.000.000/0001-00", "site": "https://beta.test"},
        telefone_contato="1133334444",
    )
    identidade = identidade_organizacao(org)
    assert identidade["nome"] == "Beta Marcas"
    assert identidade["cnpj"] == "00.000.000/0001-00"
    assert identidade["telefone"] == "1133334444"
    assert identidade["site"] == "https://beta.test"


def test_identidade_sem_nome_exibido_usa_o_nome_cadastral() -> None:
    assert identidade_organizacao(_organizacao(branding=None))["nome"] == "Escritorio Beta Ltda"


def test_proposta_dict_nao_usa_mais_o_nome_fixo_da_plataforma() -> None:
    resultado = _proposta_dict(_proposta(), _organizacao())
    assert resultado["empresa"]["nome"] == "Escritorio Beta Ltda"
    assert resultado["cliente"] == {"nome": "Maria Cliente", "email": "maria@example.com"}


# --- valores padrão por escritório -------------------------------------------------


def test_valores_padrao_vem_da_configuracao_do_escritorio() -> None:
    org = _organizacao(branding={"proposta": {"honorarios_padrao": "2200.00", "taxa_gru_padrao": "440.00"}})
    assert valores_padrao_proposta(org) == (Decimal("2200.00"), Decimal("440.00"))


def test_valores_padrao_sem_configuracao_usam_os_da_plataforma() -> None:
    assert valores_padrao_proposta(_organizacao(branding=None)) == (Decimal("1500.00"), Decimal("415.00"))
    assert valores_padrao_proposta(None) == (Decimal("1500.00"), Decimal("415.00"))
    invalido = _organizacao(branding={"proposta": {"honorarios_padrao": "abc"}})
    assert valores_padrao_proposta(invalido)[0] == Decimal("1500.00")


def test_salvar_modelo_grava_os_valores_padrao_serializaveis() -> None:
    org = _organizacao(branding={"clicksign": {"ativo": True}})
    session = FakeSession(objetos_get=[org])
    dados = PropostaConfigInput(
        titulo="PROPOSTA DE REGISTRO DE MARCA",
        escopo_padrao="Registro de marca no INPI",
        prazo_texto="Protocolo em até 24 horas úteis.",
        condicoes_texto="O protocolo não garante a concessão.",
        rodape="Rodapé do escritório",
        honorarios_padrao=Decimal("2200.00"),
        taxa_gru_padrao=Decimal("440.00"),
    )

    asyncio.run(salvar_configuracao(dados, session, usuario_teste()))

    salvo = org.branding["proposta"]
    assert Decimal(salvo["honorarios_padrao"]) == Decimal("2200.00")
    assert not isinstance(salvo["honorarios_padrao"], Decimal)
    assert org.branding["clicksign"] == {"ativo": True}
    assert _config(org)["taxa_gru_padrao"] == salvo["taxa_gru_padrao"]


def test_modelo_padrao_nao_cita_a_plataforma() -> None:
    assert "Zé Registra" not in _config(_organizacao(branding=None))["rodape"]


# --- PDF -----------------------------------------------------------------------------


def test_campos_do_modelo_sao_resolvidos() -> None:
    proposta = _proposta_dict(_proposta(), _organizacao())
    texto = resolver_campos_proposta(
        "Olá {{cliente.nome}}, proposta {{proposta.numero}} v{{proposta.versao}} de {{empresa.nome}}: "
        "{{proposta.total}}. {{campo.inexistente}}",
        proposta,
    )
    assert texto == (
        "Olá Maria Cliente, proposta PROP-2026-000010 v2 de Escritorio Beta Ltda: R$ 2.440,00. {{campo.inexistente}}"
    )


def test_pdf_da_proposta_nao_tem_mais_a_marca_nem_o_pagamento_fixos() -> None:
    fonte = inspect.getsource(gerar_pdf_proposta)
    assert "Zé Registra" not in fonte
    assert "10x" not in fonte


def test_pdf_da_proposta_leva_o_nome_do_escritorio() -> None:
    pdf = gerar_pdf_proposta(_proposta_dict(_proposta(), _organizacao()))
    assert pdf.startswith(b"%PDF")
    # O autor fica no dicionário de metadados, sem compressão.
    assert b"Escritorio Beta Ltda" in pdf


# --- e-mail ------------------------------------------------------------------------


def test_email_da_proposta_usa_o_nome_do_escritorio(monkeypatch: pytest.MonkeyPatch) -> None:
    configuracao = Settings()
    configuracao.email_enabled = True
    enviadas = []

    async def _capturar(mensagem, _settings, _operacao):
        enviadas.append(mensagem)
        return "principal"

    monkeypatch.setattr(emailing, "get_settings", lambda: configuracao)
    monkeypatch.setattr(emailing, "_enviar_smtp_contabilizado", _capturar)

    asyncio.run(
        emailing.enviar_proposta_email(
            "maria@example.com", "Maria", "https://link", b"%PDF", "PROP-1", organizacao_nome="Beta Marcas"
        )
    )

    assert enviadas[0]["Subject"] == "Proposta de registro de marca PROP-1 - Beta Marcas"
    assert "Beta Marcas" in enviadas[0].get_body(preferencelist=("plain",)).get_content()

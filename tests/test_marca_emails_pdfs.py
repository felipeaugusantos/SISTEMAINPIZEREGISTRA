"""Fase 19.3 (white-label): e-mails enviados em nome de um escritório e os
relatórios em PDF entregues ao cliente levam o nome dele. Antes, assuntos,
remetente, faixa do e-mail em HTML e o cabeçalho/rodapé dos relatórios
diziam "Zé Registra" para qualquer escritório. A organização padrão sem
marca própria continua exatamente como sempre foi.

Isolados e determinísticos: FakeSession (tests/conftest.py), sem banco real.
"""

import asyncio
from datetime import UTC, datetime

import pytest

import app.emailing as emailing
from app.cadencia_email import _nome_escritorio
from app.marca import nome_escritorio_para_email
from app.models import Organizacao
from app.relatorios import gerar_pdf_processo_monitorado
from app.settings import Settings, get_settings
from tests.conftest import FakeResult, FakeSession


def _configuracao() -> Settings:
    configuracao = Settings()
    configuracao.email_enabled = True
    return configuracao


def _organizacao(**kwargs: object) -> Organizacao:
    base: dict = {"id": 2, "nome": "Escritorio Beta Ltda", "slug": "escritorio-beta", "branding": None}
    base.update(kwargs)
    return Organizacao(**base)


# --- nome do escritório ------------------------------------------------------------


def test_nome_para_email_da_organizacao_padrao_sem_marca_e_none() -> None:
    padrao = _organizacao(id=1, nome="Zé Registra", slug=get_settings().default_organization_slug)
    assert asyncio.run(nome_escritorio_para_email(FakeSession(objetos_get=[padrao]), 1)) is None


def test_nome_para_email_de_outro_escritorio_usa_o_nome_exibido() -> None:
    org = _organizacao(branding={"nome_exibido": "Beta Marcas"})
    assert asyncio.run(nome_escritorio_para_email(FakeSession(objetos_get=[org]), 2)) == "Beta Marcas"
    assert asyncio.run(nome_escritorio_para_email(FakeSession(), None)) is None


def test_cadencia_resolve_o_nome_por_consulta() -> None:
    org = _organizacao()
    assert asyncio.run(_nome_escritorio(FakeSession([FakeResult(scalar=org)]), 2)) == "Escritorio Beta Ltda"
    assert asyncio.run(_nome_escritorio(FakeSession([FakeResult(scalar=None)]), 2)) is None


def test_falha_na_consulta_do_nome_nao_derruba_o_envio_da_cadencia() -> None:
    # Revisão do Codex no PR #155: a consulta roda num SAVEPOINT, então um
    # erro nela cai na identidade padrão sem invalidar a transação do lote.
    class _SessaoComErro(FakeSession):
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("banco indisponível")

    assert asyncio.run(_nome_escritorio(_SessaoComErro(), 2)) is None


# --- e-mails -------------------------------------------------------------------------


def test_recuperacao_de_senha_com_nome_do_escritorio() -> None:
    mensagem = emailing._mensagem_recuperacao("a@example.com", "Ana", "tok", _configuracao(), "Beta Marcas")
    assert mensagem["Subject"] == "Redefinição de senha — Beta Marcas"
    assert mensagem["From"].addresses[0].display_name == "Beta Marcas"
    html = mensagem.get_body(preferencelist=("html",)).get_content()
    assert "BETA MARCAS · CENTRO DE OPERAÇÕES" in html
    assert "ZÉ REGISTRA" not in html


def test_recuperacao_de_senha_sem_escritorio_fica_como_sempre() -> None:
    configuracao = _configuracao()
    mensagem = emailing._mensagem_recuperacao("a@example.com", "Ana", "tok", configuracao)
    assert mensagem["Subject"] == "Redefinição de senha — Zé Registra"
    assert mensagem["From"] == f"{configuracao.email_from_name} <{configuracao.email_from_address}>"
    assert "ZÉ REGISTRA® · CENTRO DE OPERAÇÕES" in mensagem.get_body(preferencelist=("html",)).get_content()


def test_exclusao_de_dados_com_nome_do_escritorio() -> None:
    mensagem = emailing._mensagem_confirmacao_exclusao("a@example.com", "tok", _configuracao(), "Beta Marcas")
    assert mensagem["Subject"] == "Confirme a exclusão dos seus dados — Beta Marcas"
    assert "BETA MARCAS · PRIVACIDADE" in mensagem.get_body(preferencelist=("html",)).get_content()


def test_passo_de_cadencia_assina_com_o_nome_do_escritorio() -> None:
    mensagem = emailing._mensagem_passo_cadencia(
        "a@example.com", "Ana", "Follow-up", "Olá!", "https://r", "https://d", _configuracao(), "Beta Marcas"
    )
    texto = mensagem.get_body(preferencelist=("plain",)).get_content()
    assert "-- \nBeta Marcas" in texto
    assert mensagem["From"].addresses[0].display_name == "Beta Marcas"


def test_nome_do_escritorio_e_saneado_nos_cabecalhos() -> None:
    mensagem = emailing._mensagem_recuperacao("a@example.com", "Ana", "tok", _configuracao(), 'Beta\r\nBcc: x <y> "z"')
    assert mensagem["Bcc"] is None
    assert mensagem["Subject"] == "Redefinição de senha — Beta Bcc: x y z"


def test_comunicacao_juridica_usa_o_nome_do_escritorio_no_assunto(monkeypatch: pytest.MonkeyPatch) -> None:
    enviadas = []

    async def _capturar(mensagem, _settings, _operacao):
        enviadas.append(mensagem)
        return "principal"

    monkeypatch.setattr(emailing, "get_settings", _configuracao)
    monkeypatch.setattr(emailing, "_enviar_smtp_contabilizado", _capturar)

    asyncio.run(
        emailing.enviar_comunicacao_juridica_rastreada(
            "juridico@example.com", "Prazo vencido", "Confira.", chave="c1", organizacao_nome="Beta Marcas"
        )
    )

    assert enviadas[0]["Subject"] == "[Beta Marcas] Prazo vencido"


# --- PDFs ----------------------------------------------------------------------------


def _dados_processo() -> dict:
    return {
        "numero": "912345678",
        "titulo": "ACME",
        "tipo": "marca",
        "data_deposito": None,
        "situacao": "Em exame",
        "titulares": ["ACME Ltda"],
        "procurador": None,
        "empresa": None,
        "responsavel": None,
        "status": "ativo",
        "etapa_kanban_label": "Em andamento",
        "movimentacoes": [],
        "observacoes_relatorio": None,
        "gerado_em": datetime.now(UTC),
        "gerado_por": "Operador",
    }


def test_relatorio_de_processo_leva_o_nome_do_escritorio() -> None:
    pdf = gerar_pdf_processo_monitorado(_dados_processo(), marca_nome="Escritorio Beta")
    assert pdf.startswith(b"%PDF")
    # O autor fica no dicionário de metadados, sem compressão.
    assert b"Escritorio Beta" in pdf


def test_relatorio_de_processo_sem_escritorio_mantem_a_plataforma() -> None:
    pdf = gerar_pdf_processo_monitorado(_dados_processo())
    assert pdf.startswith(b"%PDF")
    assert b"Escritorio Beta" not in pdf

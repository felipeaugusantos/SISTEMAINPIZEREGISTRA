import asyncio
import email
from datetime import UTC, datetime

from app.imap_polling import MensagemRecebida, _extrair_corpo, verificar_respostas_email
from app.models import RespostaEmailLead
from tests.conftest import FakeResult, FakeSession

# --- Fase 9 do plano Leads/CRM (03/09/2026), achado L6: detecção de resposta
# via IMAP fica inerte enquanto settings.imap_enabled=False (padrão).
# Achado item 21 da auditoria completa do CRM (06/09/2026): guarda o
# conteúdo da resposta (RespostaEmailLead), não só o timestamp de pausa. ---


def test_verificar_respostas_desligado_por_padrao_nao_toca_nada() -> None:
    session = FakeSession()
    resultado = asyncio.run(verificar_respostas_email(session))
    assert resultado == {"verificado": False, "motivo": "imap_desabilitado"}


def test_verificar_respostas_habilitado_registra_conteudo_e_pausa_leads() -> None:
    import app.imap_polling as modulo
    from app.settings import get_settings

    settings = get_settings()
    original_imap_enabled = settings.imap_enabled
    original_buscar = modulo._buscar_mensagens_nao_lidas
    original_pausar = modulo.pausar_envios_pendentes_do_lead

    recebido_em = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    settings.imap_enabled = True
    modulo._buscar_mensagens_nao_lidas = lambda *_a, **_k: [
        MensagemRecebida(
            endereco="fulano@example.com", assunto="Re: Proposta", corpo="Obrigado, vou analisar.", recebido_em=recebido_em
        )
    ]

    chamadas: list[tuple[int, int]] = []

    async def _pausar_fake(_session, organizacao_id, lead_id):
        chamadas.append((organizacao_id, lead_id))
        return 2

    modulo.pausar_envios_pendentes_do_lead = _pausar_fake

    try:
        session = FakeSession([FakeResult(itens=[(9, 1)])])
        resultado = asyncio.run(verificar_respostas_email(session))
    finally:
        settings.imap_enabled = original_imap_enabled
        modulo._buscar_mensagens_nao_lidas = original_buscar
        modulo.pausar_envios_pendentes_do_lead = original_pausar

    assert resultado == {"verificado": True, "remetentes": 1, "registradas": 1, "pausados": 2}
    assert chamadas == [(1, 9)]
    assert len(session.adicionados) == 1
    resposta = session.adicionados[0]
    assert isinstance(resposta, RespostaEmailLead)
    assert resposta.lead_id == 9
    assert resposta.organizacao_id == 1
    assert resposta.remetente == "fulano@example.com"
    assert resposta.assunto == "Re: Proposta"
    assert resposta.corpo == "Obrigado, vou analisar."
    assert resposta.recebido_em == recebido_em


def test_verificar_respostas_sem_lead_correspondente_nao_registra_nada() -> None:
    import app.imap_polling as modulo
    from app.settings import get_settings

    settings = get_settings()
    original_imap_enabled = settings.imap_enabled
    original_buscar = modulo._buscar_mensagens_nao_lidas

    settings.imap_enabled = True
    modulo._buscar_mensagens_nao_lidas = lambda *_a, **_k: [MensagemRecebida(endereco="desconhecido@example.com")]

    try:
        session = FakeSession([FakeResult(itens=[])])
        resultado = asyncio.run(verificar_respostas_email(session))
    finally:
        settings.imap_enabled = original_imap_enabled
        modulo._buscar_mensagens_nao_lidas = original_buscar

    assert resultado == {"verificado": True, "remetentes": 1, "registradas": 0, "pausados": 0}
    assert session.adicionados == []


def test_verificar_respostas_falha_de_conexao_nao_quebra() -> None:
    import app.imap_polling as modulo
    from app.settings import get_settings

    settings = get_settings()
    original_imap_enabled = settings.imap_enabled
    original_buscar = modulo._buscar_mensagens_nao_lidas

    def _explode(*_a: object, **_k: object) -> list[MensagemRecebida]:
        raise OSError("conexao recusada")

    settings.imap_enabled = True
    modulo._buscar_mensagens_nao_lidas = _explode
    try:
        session = FakeSession()
        resultado = asyncio.run(verificar_respostas_email(session))
    finally:
        settings.imap_enabled = original_imap_enabled
        modulo._buscar_mensagens_nao_lidas = original_buscar

    assert resultado["verificado"] is False
    assert "erro" in resultado


# --- extração de corpo ---


def test_extrair_corpo_prefere_texto_plano() -> None:
    bruto = (
        "Content-Type: multipart/alternative; boundary=\"limite\"\n\n"
        "--limite\nContent-Type: text/plain; charset=utf-8\n\n"
        "Obrigado pelo contato.\n"
        "--limite\nContent-Type: text/html; charset=utf-8\n\n"
        "<p>Obrigado <b>pelo</b> contato.</p>\n"
        "--limite--\n"
    )
    mensagem = email.message_from_string(bruto)
    assert _extrair_corpo(mensagem) == "Obrigado pelo contato."


def test_extrair_corpo_cai_para_html_removendo_tags() -> None:
    bruto = "Content-Type: text/html; charset=utf-8\n\n<p>Olá <b>mundo</b>!</p>\n"
    mensagem = email.message_from_string(bruto)
    assert "mundo" in _extrair_corpo(mensagem)
    assert "<" not in _extrair_corpo(mensagem)


def test_extrair_corpo_trunca_texto_muito_longo() -> None:
    from app.imap_polling import TAMANHO_MAXIMO_CORPO

    corpo_longo = "a" * (TAMANHO_MAXIMO_CORPO + 500)
    bruto = f"Content-Type: text/plain; charset=utf-8\n\n{corpo_longo}\n"
    mensagem = email.message_from_string(bruto)
    assert len(_extrair_corpo(mensagem)) == TAMANHO_MAXIMO_CORPO


def test_extrair_corpo_sem_conteudo_devolve_vazio() -> None:
    mensagem = email.message_from_string("Content-Type: text/plain\n\n")
    assert _extrair_corpo(mensagem) == ""

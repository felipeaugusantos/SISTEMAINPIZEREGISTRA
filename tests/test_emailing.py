import asyncio
import smtplib
from email.message import EmailMessage

import pytest

from app.emailing import (
    _enviar_smtp,
    _link_recuperacao,
    _mensagem_recuperacao,
    _registrar_email_rejeitado,
    enviar_alerta_nova_pesquisa,
    enviar_alerta_novo_lead,
    enviar_email_prospeccao_lead,
    erro_cota_diaria_email,
)
from app.models import EventoOperacional
from app.settings import Settings
from tests.conftest import FakeSession


def configuracao_email() -> Settings:
    return Settings(
        app_public_url="https://app.zeregistra.test/",
        email_enabled=True,
        email_from_address="nao-responda@zeregistra.test",
        email_from_name="Zé Registra",
        smtp_host="mailpit",
        smtp_port=1025,
        smtp_starttls=False,
    )


def test_link_usa_fragmento_para_nao_expor_token_em_logs() -> None:
    link = _link_recuperacao(configuracao_email(), "token com espaco")
    assert link == "https://app.zeregistra.test/redefinir-senha#token=token%20com%20espaco"
    assert "?token=" not in link


def test_mensagem_tem_texto_html_destinatario_e_validade() -> None:
    mensagem = _mensagem_recuperacao(
        "enzo@example.test",
        "Enzo <Admin>",
        "segredo",
        configuracao_email(),
    )
    assert mensagem["To"] == "enzo@example.test"
    assert mensagem["From"] == "Zé Registra <nao-responda@zeregistra.test>"
    assert mensagem.is_multipart()
    texto = mensagem.get_body(preferencelist=("plain",)).get_content()
    html = mensagem.get_body(preferencelist=("html",)).get_content()
    assert "30 minutos" in texto
    assert "#token=segredo" in texto
    assert "Enzo &lt;Admin&gt;" in html


def test_envio_smtp_sem_tls_nem_autenticacao(monkeypatch) -> None:
    eventos: list[tuple] = []

    class SmtpFalso:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            eventos.append(("conectar", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            eventos.append(("fechar",))

        def starttls(self, **_kwargs) -> None:
            eventos.append(("tls",))

        def login(self, *_args) -> None:
            eventos.append(("login",))

        def send_message(self, mensagem: EmailMessage) -> None:
            eventos.append(("enviar", mensagem["To"]))

    monkeypatch.setattr("app.emailing.smtplib.SMTP", SmtpFalso)
    settings = configuracao_email()
    mensagem = _mensagem_recuperacao("enzo@example.test", "Enzo", "segredo", settings)
    _enviar_smtp(mensagem, settings)

    assert eventos == [
        ("conectar", "mailpit", 1025, 10.0),
        ("enviar", "enzo@example.test"),
        ("fechar",),
    ]


# --- Indicador "e-mails rejeitados" (Fase 6, docs/slo-e-criterios-incidente.md):
# _registrar_email_rejeitado só grava quando o SMTP recusa a mensagem de vez
# (5xx) -- erro transitório de conexão/timeout não é uma rejeição. ---


class _SessionFactoryFalsa:
    """Espelha o `async with session_factory() as session` usado por
    _registrar_email_rejeitado -- FakeSession não é um gerenciador de
    contexto assíncrono por padrão (é injetada via dependência HTTP nos
    outros testes), então este teste monta um mínimo."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> "_SessionFactoryFalsa":
        return self

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_registrar_email_rejeitado_grava_em_rejeicao_smtp_explicita(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr("app.emailing.session_factory", _SessionFactoryFalsa(session))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    erro = smtplib.SMTPRecipientsRefused({"destino@example.test": (550, b"Mailbox unavailable")})

    asyncio.run(_registrar_email_rejeitado("recuperacao_senha", erro))

    assert session.commits == 1
    evento = session.adicionados[0]
    assert isinstance(evento, EventoOperacional)
    assert evento.componente == "email"
    assert evento.operacao == "recuperacao_senha"
    assert evento.sucesso is False
    assert evento.codigo_erro == "SMTPRecipientsRefused"


def test_registrar_email_rejeitado_ignora_erro_transitorio(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr("app.emailing.session_factory", _SessionFactoryFalsa(session))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    asyncio.run(_registrar_email_rejeitado("recuperacao_senha", TimeoutError("conexao expirou")))

    assert session.commits == 0
    assert session.adicionados == []


def test_reconhece_limite_diario_do_provedor() -> None:
    erro = smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded")
    assert erro_cota_diaria_email(erro) is True
    assert erro_cota_diaria_email(smtplib.SMTPDataError(550, b"Mailbox unavailable")) is False


def test_cota_diaria_interrompe_retentativas_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    tentativas = 0

    def _smtp_esgotado(*_args: object, **_kwargs: object) -> None:
        nonlocal tentativas
        tentativas += 1
        raise smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded")

    async def _ignorar_registro(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    monkeypatch.setattr("app.emailing._enviar_smtp", _smtp_esgotado)
    monkeypatch.setattr("app.emailing._registrar_email_rejeitado", _ignorar_registro)

    with pytest.raises(smtplib.SMTPDataError):
        asyncio.run(enviar_email_prospeccao_lead("lead@example.test", "Assunto", "Conteúdo"))

    assert tentativas == 1


# --- Achado P1 do Codex no PR #133 (Fase 15.2, 23/09/2026): um fallback
# pra settings.equipe_atendimento_email (env var global do processo)
# recriava o vazamento entre tenants que a fase corrige -- toda
# organização sem e-mail de alerta configurado mandaria dados de lead
# pra uma caixa de e-mail de OUTRO tenant. Sem destinatário configurado
# pra esta organização, o alerta simplesmente não dispara. ---


def test_alerta_novo_lead_nao_envia_sem_destinatario_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    enviados: list[str] = []
    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    monkeypatch.setattr("app.emailing._enviar_smtp", lambda msg, _settings: enviados.append(msg["To"]))

    asyncio.run(enviar_alerta_novo_lead("Fulano", "fulano@example.test", "11999999999", "Marca", "geral"))

    assert enviados == []


def test_alerta_novo_lead_envia_para_destinatario_da_organizacao(monkeypatch: pytest.MonkeyPatch) -> None:
    enviados: list[str] = []
    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    monkeypatch.setattr("app.emailing._enviar_smtp", lambda msg, _settings: enviados.append(msg["To"]))

    asyncio.run(
        enviar_alerta_novo_lead(
            "Fulano", "fulano@example.test", "11999999999", "Marca", "geral", "equipe@empresa.com.br"
        )
    )

    assert enviados == ["equipe@empresa.com.br"]


def test_alerta_nova_pesquisa_nao_envia_sem_destinatario_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    enviados: list[str] = []
    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    monkeypatch.setattr("app.emailing._enviar_smtp", lambda msg, _settings: enviados.append(msg["To"]))

    asyncio.run(enviar_alerta_nova_pesquisa("Marca", "Fulano", None))

    assert enviados == []

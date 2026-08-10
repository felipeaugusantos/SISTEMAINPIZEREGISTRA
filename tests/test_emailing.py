from email.message import EmailMessage

from app.emailing import _enviar_smtp, _link_recuperacao, _mensagem_recuperacao
from app.settings import Settings


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

from __future__ import annotations

import asyncio
import html
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote

from app.settings import Settings, get_settings


def _link_recuperacao(settings: Settings, token: str) -> str:
    base = settings.app_public_url.rstrip("/")
    # O fragmento não é enviado ao servidor nem incluído em logs HTTP/referrers.
    return f"{base}/redefinir-senha#token={quote(token, safe='')}"


def _mensagem_recuperacao(
    destinatario: str, nome: str, token: str, settings: Settings
) -> EmailMessage:
    link = _link_recuperacao(settings, token)
    nome_seguro = html.escape(nome or "usuário")
    minutos = settings.password_reset_minutes
    mensagem = EmailMessage()
    mensagem["Subject"] = "Redefinição de senha — Zé Registra"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    mensagem.set_content(
        f"Olá, {nome or 'usuário'}.\n\n"
        "Recebemos uma solicitação para redefinir sua senha no Centro de Operações.\n"
        f"Acesse o link abaixo em até {minutos} minutos:\n\n{link}\n\n"
        "Se você não solicitou a alteração, ignore esta mensagem."
    )
    mensagem.add_alternative(
        f"""
        <!doctype html>
        <html lang="pt-BR"><body style="margin:0;background:#f5f3eb;padding:28px;">
          <main style="max-width:600px;margin:auto;background:#fff;border:1px solid #d8ddd6;
                       border-radius:20px;padding:36px;font-family:Arial,sans-serif;color:#10251d;">
            <p style="margin:0;color:#08704d;font-weight:700;letter-spacing:.08em;">
              ZÉ REGISTRA® · CENTRO DE OPERAÇÕES
            </p>
            <h1 style="font-size:30px;margin:22px 0 12px;">Redefina sua senha</h1>
            <p>Olá, {nome_seguro}.</p>
            <p>Recebemos uma solicitação para redefinir sua senha. Este link é individual,
               pode ser utilizado uma única vez e expira em {minutos} minutos.</p>
            <p style="margin:28px 0;">
              <a href="{html.escape(link, quote=True)}"
                 style="display:inline-block;background:#086044;color:#fff;text-decoration:none;
                        border-radius:10px;padding:14px 22px;font-weight:700;">
                Criar nova senha
              </a>
            </p>
            <p style="color:#607068;font-size:13px;">
              Se você não solicitou a alteração, ignore esta mensagem. Sua senha continuará válida.
            </p>
          </main>
        </body></html>
        """,
        subtype="html",
    )
    return mensagem


def _enviar_smtp(mensagem: EmailMessage, settings: Settings) -> None:
    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
    ) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(mensagem)


async def enviar_recuperacao_senha(destinatario: str, nome: str, token: str) -> None:
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = _mensagem_recuperacao(destinatario, nome, token, settings)
    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, settings.smtp_max_attempts) + 1):
        try:
            await asyncio.to_thread(_enviar_smtp, mensagem, settings)
            return
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
        raise ultimo_erro


async def enviar_recuperacao_portal(destinatario: str, nome: str, token: str) -> None:
    """Envia recuperação do portal sem reutilizar o link do Centro de Operações."""
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = "Recuperação de acesso ao Portal do cliente — Zé Registra"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    link = f"{settings.app_public_url.rstrip('/')}/portal#recuperacao={quote(token, safe='')}"
    mensagem.set_content(f"Olá, {nome or 'cliente'}.\n\nAcesse o portal para redefinir seu acesso:\n{link}\n\nO link expira em 30 minutos e pode ser usado uma única vez.")
    await asyncio.to_thread(_enviar_smtp, mensagem, settings)


async def enviar_proposta_email(destinatario: str, nome: str, link: str, pdf_bytes: bytes, numero: str) -> None:
    """Envia a proposta com link seguro e PDF anexado, quando SMTP estiver habilitado."""
    settings = get_settings()
    if not settings.email_enabled:
        return
    mensagem = EmailMessage()
    mensagem["Subject"] = f"Proposta de registro de marca {numero} - Zé Registra"
    mensagem["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    mensagem["To"] = destinatario
    mensagem.set_content(
        f"Olá, {nome or 'cliente'}.\n\n"
        "Sua proposta de registro de marca está disponível no link abaixo:\n\n"
        f"{link}\n\n"
        "O PDF da proposta também está anexado."
    )
    mensagem.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=f"proposta-{numero}.pdf")
    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, settings.smtp_max_attempts) + 1):
        try:
            await asyncio.to_thread(_enviar_smtp, mensagem, settings)
            return
        except Exception as exc:
            ultimo_erro = exc
            if tentativa < settings.smtp_max_attempts:
                await asyncio.sleep(min(2 ** (tentativa - 1), 4))
    if ultimo_erro is not None:
        raise ultimo_erro

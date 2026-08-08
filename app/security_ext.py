import base64
import hashlib
import hmac
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from app.settings import get_settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().security_master_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def proteger_segredo(valor: str) -> str:
    return _fernet().encrypt(valor.encode()).decode()


def revelar_segredo(valor: str) -> str:
    try:
        return _fernet().decrypt(valor.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Chave mestra incorreta para o segredo protegido") from exc


def gerar_segredo_totp() -> str:
    import secrets

    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def codigo_totp(segredo: str, instante: int | None = None) -> str:
    instante = int(time.time()) if instante is None else instante
    contador = instante // 30
    padding = "=" * ((8 - len(segredo) % 8) % 8)
    chave = base64.b32decode(segredo + padding, casefold=True)
    resumo = hmac.new(chave, struct.pack(">Q", contador), hashlib.sha1).digest()
    deslocamento = resumo[-1] & 0x0F
    numero = (
        struct.unpack(">I", resumo[deslocamento : deslocamento + 4])[0] & 0x7FFFFFFF
    ) % 1_000_000
    return f"{numero:06d}"


def validar_totp(segredo: str, codigo: str, instante: int | None = None) -> bool:
    agora = int(time.time()) if instante is None else instante
    return any(
        hmac.compare_digest(codigo_totp(segredo, agora + desvio), codigo.strip())
        for desvio in (-30, 0, 30)
    )


def uri_totp(segredo: str, email: str) -> str:
    emissor = "Ze Registra"
    return f"otpauth://totp/{quote(emissor)}:{quote(email)}?secret={segredo}&issuer={quote(emissor)}&digits=6&period=30"

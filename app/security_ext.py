import base64
import hashlib
import hmac
import re
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from app.settings import get_settings


def validar_forca_senha(senha: str) -> str:
    requisitos = (
        re.search(r"[a-z]", senha),
        re.search(r"[A-Z]", senha),
        re.search(r"\d", senha),
        re.search(r"[^A-Za-z0-9]", senha),
    )
    if not all(requisitos):
        raise ValueError("A senha deve conter letra maiúscula, minúscula, número e caractere especial")
    return senha


def _fernet(chave: str) -> Fernet:
    digest = hashlib.sha256(chave.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def versao_chave_atual() -> int:
    return get_settings().security_master_key_version


def _chaves_disponiveis() -> dict[int, Fernet]:
    settings = get_settings()
    chaves = {settings.security_master_key_version: _fernet(settings.security_master_key)}
    if settings.security_master_key_previous and settings.security_master_key_previous_version > 0:
        chaves[settings.security_master_key_previous_version] = _fernet(settings.security_master_key_previous)
    return chaves


def proteger_segredo(valor: str) -> str:
    versao = versao_chave_atual()
    token = _chaves_disponiveis()[versao].encrypt(valor.encode()).decode()
    return f"v{versao}:{token}"


def revelar_segredo(valor: str) -> str:
    chaves = _chaves_disponiveis()
    correspondencia = re.match(r"^v([1-9][0-9]*):(gAAAA.+)$", valor)
    if correspondencia:
        versao = int(correspondencia.group(1))
        candidatos = [(versao, chaves.get(versao))]
        token = correspondencia.group(2)
    else:
        # Ciphertexts anteriores a esta fase nao tinham prefixo. Tenta a chave
        # atual e, durante a janela de rotacao, a chave anterior.
        candidatos = list(chaves.items())
        token = valor
    for _versao, fernet in candidatos:
        if fernet is None:
            continue
        try:
            return fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            continue
    raise ValueError("Chave mestra incorreta ou versao indisponivel para o segredo protegido")


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
    numero = (struct.unpack(">I", resumo[deslocamento : deslocamento + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{numero:06d}"


def validar_totp(segredo: str, codigo: str, instante: int | None = None) -> bool:
    agora = int(time.time()) if instante is None else instante
    return any(hmac.compare_digest(codigo_totp(segredo, agora + desvio), codigo.strip()) for desvio in (-30, 0, 30))


def uri_totp(segredo: str, email: str) -> str:
    emissor = "Ze Registra"
    return f"otpauth://totp/{quote(emissor)}:{quote(email)}?secret={segredo}&issuer={quote(emissor)}&digits=6&period=30"

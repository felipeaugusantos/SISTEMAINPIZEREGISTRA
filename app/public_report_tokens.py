import base64
import hashlib
import hmac
import json
import time

from app.settings import get_settings

TOKEN_RELATORIO_TTL_SEGUNDOS = 15 * 60
_AUDIENCIA = "relatorio-publico"
_CONTEXTO_CHAVE = b"zeregistra:relatorio-publico:v1"


class TokenRelatorioInvalido(ValueError):
    pass


def _base64_url_encode(valor: bytes) -> str:
    return base64.urlsafe_b64encode(valor).rstrip(b"=").decode("ascii")


def _base64_url_decode(valor: str) -> bytes:
    padding = "=" * (-len(valor) % 4)
    return base64.b64decode(valor + padding, altchars=b"-_", validate=True)


def _chave_assinatura(chave_mestra: str) -> bytes:
    return hmac.new(chave_mestra.encode("utf-8"), _CONTEXTO_CHAVE, hashlib.sha256).digest()


def emitir_token_relatorio(
    pesquisa_id: str,
    organizacao_id: int,
    *,
    agora: int | None = None,
    ttl_segundos: int = TOKEN_RELATORIO_TTL_SEGUNDOS,
) -> str:
    instante = int(time.time()) if agora is None else agora
    payload = {
        "aud": _AUDIENCIA,
        "exp": instante + ttl_segundos,
        "oid": organizacao_id,
        "rid": str(pesquisa_id),
        "v": 1,
    }
    payload_codificado = _base64_url_encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    assinatura = hmac.new(
        _chave_assinatura(get_settings().security_master_key), payload_codificado.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload_codificado}.{_base64_url_encode(assinatura)}"


def validar_token_relatorio(
    token: str,
    pesquisa_id: str,
    organizacao_id: int,
    *,
    agora: int | None = None,
) -> None:
    if not token or len(token) > 2048:
        raise TokenRelatorioInvalido("Token de relatório ausente ou inválido")
    try:
        payload_codificado, assinatura_codificada = token.split(".", 1)
        assinatura = _base64_url_decode(assinatura_codificada)
        payload = json.loads(_base64_url_decode(payload_codificado))
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise TokenRelatorioInvalido("Token de relatório inválido") from exc
    if not isinstance(payload, dict):
        raise TokenRelatorioInvalido("Payload do token de relatório inválido")

    settings = get_settings()
    chaves = [settings.security_master_key]
    if settings.security_master_key_previous:
        chaves.append(settings.security_master_key_previous)
    assinatura_valida = any(
        hmac.compare_digest(
            assinatura,
            hmac.new(_chave_assinatura(chave), payload_codificado.encode("ascii"), hashlib.sha256).digest(),
        )
        for chave in chaves
    )
    if not assinatura_valida:
        raise TokenRelatorioInvalido("Assinatura do token de relatório inválida")

    instante = int(time.time()) if agora is None else agora
    if payload.get("aud") != _AUDIENCIA or payload.get("v") != 1:
        raise TokenRelatorioInvalido("Escopo do token de relatório inválido")
    if not isinstance(payload.get("exp"), int) or payload["exp"] <= instante:
        raise TokenRelatorioInvalido("Token de relatório expirado")
    if payload.get("oid") != organizacao_id or not hmac.compare_digest(str(payload.get("rid")), str(pesquisa_id)):
        raise TokenRelatorioInvalido("Token não autorizado para este relatório")

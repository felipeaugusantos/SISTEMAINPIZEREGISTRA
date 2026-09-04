from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.portal_cliente import _escanear_upload
from app.malware_scan import ArquivoInfectadoError, ScannerIndisponivelError

# --- Achado FASE6-13 da auditoria (04/09/2026): varredura de malware antes
# de aceitar upload no portal do cliente (app/api/portal_cliente.py). ---


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "clamav_enabled": True,
        "clamav_host": "clamav",
        "clamav_port": 3310,
        "clamav_timeout_seconds": 5.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_escanear_upload_desligado_nao_chama_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    chamado = False

    def _escanear(*_args: object, **_kwargs: object) -> None:
        nonlocal chamado
        chamado = True

    monkeypatch.setattr("app.api.portal_cliente.get_settings", lambda: _settings(clamav_enabled=False))
    monkeypatch.setattr("app.api.portal_cliente.escanear", _escanear)

    await _escanear_upload(b"qualquer coisa")

    assert chamado is False


async def test_escanear_upload_limpo_nao_levanta_excecao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.portal_cliente.get_settings", lambda: _settings())
    monkeypatch.setattr("app.api.portal_cliente.escanear", lambda *_a, **_k: None)

    await _escanear_upload(b"conteudo limpo")


async def test_escanear_upload_infectado_retorna_422(monkeypatch: pytest.MonkeyPatch) -> None:
    def _escanear(*_args: object, **_kwargs: object) -> None:
        raise ArquivoInfectadoError("Eicar-Test-Signature")

    monkeypatch.setattr("app.api.portal_cliente.get_settings", lambda: _settings())
    monkeypatch.setattr("app.api.portal_cliente.escanear", _escanear)

    with pytest.raises(HTTPException) as exc_info:
        await _escanear_upload(b"conteudo malicioso")

    assert exc_info.value.status_code == 422


async def test_escanear_upload_scanner_indisponivel_falha_fechada_retorna_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _escanear(*_args: object, **_kwargs: object) -> None:
        raise ScannerIndisponivelError("timeout")

    monkeypatch.setattr("app.api.portal_cliente.get_settings", lambda: _settings())
    monkeypatch.setattr("app.api.portal_cliente.escanear", _escanear)

    with pytest.raises(HTTPException) as exc_info:
        await _escanear_upload(b"conteudo")

    # Falha fechada: scanner fora do ar -> upload recusado, nunca aceito sem confirmação.
    assert exc_info.value.status_code == 503

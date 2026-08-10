import pytest
from fastapi import HTTPException

from app import ratelimit
from app.ratelimit import RateLimiter


def test_limita_em_memoria_apos_estourar_a_janela() -> None:
    limitador = RateLimiter(limite=3, janela_segundos=60, escopo="teste-mem")
    for _ in range(3):
        limitador.aplicar("ip")
    with pytest.raises(HTTPException) as exc:
        limitador.aplicar("ip")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_limpar_reseta_a_contagem() -> None:
    limitador = RateLimiter(limite=1, janela_segundos=60, escopo="teste-limpar")
    limitador.aplicar("ip")
    limitador.limpar()
    limitador.aplicar("ip")  # não deve levantar


def test_fallback_para_memoria_quando_redis_indisponivel(monkeypatch) -> None:
    class FakeSettings:
        ratelimit_redis_enabled = True
        redis_url = "redis://127.0.0.1:6390/0"  # porta fechada de propósito

    monkeypatch.setattr(ratelimit, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(ratelimit, "_cliente_redis", None)
    monkeypatch.setattr(ratelimit, "_redis_indisponivel_ate", 0.0)

    limitador = RateLimiter(limite=2, janela_segundos=60, escopo="teste-fallback")
    limitador.aplicar("ip")  # Redis falha -> abre circuito -> memória (1)
    limitador.aplicar("ip")  # circuito aberto -> memória (2)
    with pytest.raises(HTTPException):
        limitador.aplicar("ip")  # memória atinge o limite

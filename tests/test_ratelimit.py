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


def test_limitador_sensivel_aperta_o_teto_quando_o_redis_esta_degradado(monkeypatch) -> None:
    # Auditoria A10: com o Redis habilitado mas fora do ar, login/recuperação usam metade do limite local.
    from types import SimpleNamespace

    monkeypatch.setattr(ratelimit, "get_settings", lambda: SimpleNamespace(ratelimit_redis_enabled=True, redis_url=""))
    monkeypatch.setattr(ratelimit, "_redis_indisponivel_ate", float("inf"))  # circuito aberto
    sensivel = RateLimiter(limite=10, janela_segundos=60, escopo="teste-sensivel", sensivel=True)
    comum = RateLimiter(limite=10, janela_segundos=60, escopo="teste-comum")

    for _ in range(5):
        sensivel.aplicar("ip")
        comum.aplicar("ip")
    with pytest.raises(HTTPException):
        sensivel.aplicar("ip")  # 6ª tentativa: teto degradado = 5
    comum.aplicar("ip")  # o limitador comum segue com o limite normal


def test_circuito_do_redis_registra_alerta_de_degradacao(monkeypatch, caplog) -> None:
    import logging

    monkeypatch.setattr(ratelimit, "_redis_indisponivel_ate", 0.0)
    with caplog.at_level(logging.ERROR, logger="ze_registra.ratelimit"):
        ratelimit._abrir_circuito()
        ratelimit._abrir_circuito()  # dentro do cooldown: não repete o alerta
    assert [r.message for r in caplog.records].count('{"event": "RATELIMIT_REDIS_DEGRADADO", "cooldown_s": 30.0}') == 1

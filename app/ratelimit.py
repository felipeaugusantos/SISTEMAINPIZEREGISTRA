import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.proxy import cliente_ip
from app.settings import get_settings

# Circuit breaker compartilhado: se o Redis falhar, para de tentar por um intervalo
# curto e usa a memória local, evitando pagar timeout em toda requisição.
_COOLDOWN_REDIS_SEGUNDOS = 30.0
_redis_indisponivel_ate = 0.0
_cliente_redis = None


def _obter_cliente_redis():
    global _cliente_redis
    if _cliente_redis is None:
        import redis  # importado sob demanda; só necessário quando habilitado

        _cliente_redis = redis.Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            decode_responses=True,
        )
    return _cliente_redis


class RateLimiter:
    """Limitador de taxa por chave com janela deslizante.

    Usa Redis (sorted set) quando RATELIMIT_REDIS_ENABLED está ativo, permitindo
    múltiplos workers/instâncias compartilharem o mesmo limite. Se o Redis estiver
    indisponível, degrada de forma transparente para uma janela em memória local.
    """

    def __init__(
        self,
        limite: int,
        janela_segundos: float,
        *,
        escopo: str = "geral",
        maximo_chaves: int = 20_000,
    ) -> None:
        self.limite = limite
        self.janela = janela_segundos
        self.escopo = escopo
        self.maximo_chaves = maximo_chaves
        self._acessos: dict[str, deque[float]] = defaultdict(deque)

    def _cliente(self, request: Request) -> str:
        return cliente_ip(request)

    def __call__(self, request: Request) -> None:
        self.aplicar(f"{request.url.path}:{self._cliente(request)}")

    def _excedido(self, espera: int) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas solicitações. Tente novamente em instantes.",
            headers={"Retry-After": str(espera)},
        )

    def aplicar(self, chave: str) -> None:
        """Registra uma tentativa para uma chave definida pelo chamador."""
        chave_escopo = f"{self.escopo}:{chave}"
        if get_settings().ratelimit_redis_enabled and time.monotonic() >= _redis_indisponivel_ate:
            try:
                self._aplicar_redis(chave_escopo)
                return
            except HTTPException:
                raise
            except Exception:
                _abrir_circuito()
        self._aplicar_memoria(chave_escopo)

    def _aplicar_redis(self, chave_escopo: str) -> None:
        cliente = _obter_cliente_redis()
        chave = f"ratelimit:{chave_escopo}"
        agora = time.time()
        pipe = cliente.pipeline()
        pipe.zremrangebyscore(chave, 0, agora - self.janela)
        pipe.zcard(chave)
        atual = pipe.execute()[1]
        if atual >= self.limite:
            mais_antigo = cliente.zrange(chave, 0, 0, withscores=True)
            espera = 1
            if mais_antigo:
                espera = max(1, int(self.janela - (agora - mais_antigo[0][1])) + 1)
            raise self._excedido(espera)
        pipe = cliente.pipeline()
        pipe.zadd(chave, {f"{agora}:{secrets.token_hex(6)}": agora})
        pipe.expire(chave, int(self.janela) + 1)
        pipe.execute()

    def _aplicar_memoria(self, chave_escopo: str) -> None:
        agora = time.monotonic()
        registros = self._acessos[chave_escopo]
        while registros and agora - registros[0] > self.janela:
            registros.popleft()
        if len(registros) >= self.limite:
            espera = max(1, int(self.janela - (agora - registros[0])) + 1)
            raise self._excedido(espera)
        registros.append(agora)
        if len(self._acessos) > self.maximo_chaves:
            expiradas = [
                item
                for item, acessos in self._acessos.items()
                if not acessos or agora - acessos[-1] > self.janela
            ]
            for item in expiradas:
                self._acessos.pop(item, None)

    def limpar(self) -> None:
        self._acessos.clear()


def _abrir_circuito() -> None:
    global _redis_indisponivel_ate
    _redis_indisponivel_ate = time.monotonic() + _COOLDOWN_REDIS_SEGUNDOS

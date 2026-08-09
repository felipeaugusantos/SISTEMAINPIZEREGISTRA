import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class RateLimiter:
    """Limitador de taxa em memória, por IP, com janela deslizante.

    Adequado a um único processo (a implantação atual usa um worker uvicorn).
    Para múltiplos workers/instâncias, trocar por um backend compartilhado (Redis).
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
        return request.client.host if request.client else "desconhecido"

    def __call__(self, request: Request) -> None:
        self.aplicar(f"{request.url.path}:{self._cliente(request)}")

    def aplicar(self, chave: str) -> None:
        """Registra uma tentativa para uma chave definida pelo chamador."""
        agora = time.monotonic()
        chave_escopo = f"{self.escopo}:{chave}"
        registros = self._acessos[chave_escopo]
        while registros and agora - registros[0] > self.janela:
            registros.popleft()
        if len(registros) >= self.limite:
            espera = max(1, int(self.janela - (agora - registros[0])) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Muitas solicitações. Tente novamente em instantes.",
                headers={"Retry-After": str(espera)},
            )
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

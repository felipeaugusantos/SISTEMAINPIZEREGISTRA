import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class RateLimiter:
    """Limitador de taxa em memória, por IP, com janela deslizante.

    Adequado a um único processo (a implantação atual usa um worker uvicorn).
    Para múltiplos workers/instâncias, trocar por um backend compartilhado (Redis).
    """

    def __init__(self, limite: int, janela_segundos: float) -> None:
        self.limite = limite
        self.janela = janela_segundos
        self._acessos: dict[str, deque[float]] = defaultdict(deque)

    def _cliente(self, request: Request) -> str:
        return request.client.host if request.client else "desconhecido"

    def __call__(self, request: Request) -> None:
        self.aplicar(self._cliente(request))

    def aplicar(self, chave: str) -> None:
        """Registra uma tentativa para uma chave definida pelo chamador."""
        agora = time.monotonic()
        registros = self._acessos[chave]
        while registros and agora - registros[0] > self.janela:
            registros.popleft()
        if len(registros) >= self.limite:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Muitas solicitações. Tente novamente em instantes.",
                headers={"Retry-After": str(int(self.janela))},
            )
        registros.append(agora)

    def limpar(self) -> None:
        self._acessos.clear()

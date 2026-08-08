import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.settings import get_settings

bearer = HTTPBearer(auto_error=False)


def exigir_token_integracao(
    request: Request,
    credenciais: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
) -> None:
    settings = get_settings()

    if not settings.integration_auth_enabled:
        return

    token = request.headers.get("X-Integration-Key")
    if not token and credenciais is not None and credenciais.scheme.lower() == "bearer":
        token = credenciais.credentials
    if token and secrets.compare_digest(token, settings.inpi_integration_token):
        request.state.global_integration_token = True
        return
    # Chaves SaaS sÃ£o validadas pelo resolvedor de organizaÃ§Ã£o, que possui acesso ao banco.
    if token:
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token de integração inválido",
        headers={"WWW-Authenticate": "Bearer"},
    )

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.public_report_tokens import TokenRelatorioInvalido, validar_token_relatorio
from app.settings import get_settings
from app.tenancy import OrganizacaoPublicaDep

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
    # Chaves SaaS são validadas pelo resolvedor de organização, que possui acesso ao banco.
    # Aqui só sinalizamos que há um token candidato; a validação efetiva ocorre no resolvedor.
    if token:
        request.state.integration_token_candidato = True
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token de integração inválido",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def exigir_token_relatorio_publico(
    pesquisa_id: str,
    request: Request,
    organizacao: OrganizacaoPublicaDep,
) -> None:
    token = request.headers.get("X-Report-Token", "").strip()
    try:
        validar_token_relatorio(token, pesquisa_id, organizacao.id)
    except TokenRelatorioInvalido as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token temporário de relatório inválido ou expirado",
            headers={"WWW-Authenticate": "Report-Token"},
        ) from exc


AcessoRelatorioPublicoDep = Annotated[None, Depends(exigir_token_relatorio_publico)]

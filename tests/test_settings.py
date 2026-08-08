import pytest
from pydantic import ValidationError

from app.settings import Settings


def test_producao_rejeita_senha_padrao() -> None:
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
        Settings(app_env="production", admin_password="altere-esta-senha")


def test_producao_rejeita_senha_curta() -> None:
    with pytest.raises(ValidationError, match="ao menos"):
        Settings(app_env="production", admin_password="curta")


def test_producao_aceita_senha_forte() -> None:
    settings = Settings(
        app_env="production",
        admin_password="Uma-Senha-Bem-Forte-2026",
        audit_ip_salt="segredo-de-auditoria-forte",
        security_master_key="segredo-mestre-com-mais-de-trinta-e-dois-caracteres",
        database_url="postgresql+asyncpg://app:senha-forte@db:5432/inpi",
        admin_force_https=True,
        integration_auth_enabled=True,
        inpi_integration_token="token-de-integracao-com-mais-de-32-caracteres",
        cors_allowed_origins="https://consulta.exemplo.com.br",
    )
    assert settings.app_env == "production"


def test_desenvolvimento_nao_valida_senha() -> None:
    settings = Settings(app_env="development", admin_password="")
    assert settings.admin_password == ""


def test_producao_rejeita_salt_de_auditoria_curto() -> None:
    with pytest.raises(ValidationError, match="AUDIT_IP_SALT"):
        Settings(
            app_env="production",
            admin_password="Uma-Senha-Bem-Forte-2026",
            audit_ip_salt="curto",
        )


def test_autenticacao_de_integracao_rejeita_token_curto() -> None:
    with pytest.raises(ValidationError, match="INPI_INTEGRATION_TOKEN"):
        Settings(
            integration_auth_enabled=True,
            inpi_integration_token="token-curto",
        )


def test_autenticacao_de_integracao_aceita_token_forte() -> None:
    settings = Settings(
        integration_auth_enabled=True,
        inpi_integration_token="token-de-integracao-com-mais-de-32-caracteres",
    )
    assert settings.integration_auth_enabled is True

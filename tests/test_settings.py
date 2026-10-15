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


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("audit_ip_salt", "desenvolvimento-local"),
        ("audit_ip_salt", "defina-um-segredo-de-auditoria"),
        ("security_master_key", "desenvolvimento-local-chave-mestra"),
        ("security_master_key", "gere-uma-chave-mestra-local-com-mais-de-32-caracteres"),
    ],
)
def test_producao_rejeita_segredos_padrao_de_desenvolvimento(campo: str, valor: str) -> None:
    dados = {
        "app_env": "production",
        "admin_password": "Uma-Senha-Bem-Forte-2026",
        "audit_ip_salt": "segredo-de-auditoria-forte",
        "security_master_key": "segredo-mestre-com-mais-de-trinta-e-dois-caracteres",
        campo: valor,
    }
    with pytest.raises(ValidationError, match=campo.upper()):
        Settings(**dados)


def test_compose_da_api_nao_recebe_credencial_de_migracao() -> None:
    from pathlib import Path

    compose = Path(__file__).resolve().parent.parent / "compose.yaml"
    trecho_api = compose.read_text(encoding="utf-8").split("\n  api:\n", 1)[1].split("\n  worker:", 1)[0]
    assert "MIGRATION_DATABASE_URL" not in trecho_api


def test_compose_propaga_variaveis_de_webhook_e_storage() -> None:
    # Auditoria A12: variável só no .env do host não chega ao container se o compose não a mapear.
    from pathlib import Path

    texto = (Path(__file__).resolve().parent.parent / "compose.yaml").read_text(encoding="utf-8")
    api = texto.split("\n  api:\n", 1)[1].split("\n  worker:", 1)[0]
    worker = texto.split("\n  worker:\n", 1)[1].X
    for variavel in ("GATEWAY_WEBHOOK_SECRET", "STORAGE_BACKEND", "STORAGE_S3_BUCKET", "STORAGE_S3_SECRET_KEY"):
        assert f"{variavel}:" in api
    for variavel in ("STORAGE_BACKEND", "STORAGE_S3_BUCKET", "STORAGE_S3_SECRET_KEY"):
        assert f"{variavel}:" in worker


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

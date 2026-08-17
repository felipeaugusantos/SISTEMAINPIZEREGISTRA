from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SENHAS_PLACEHOLDER = frozenset({"", "altere-esta-senha", "defina-uma-senha-forte"})
TAMANHO_MINIMO_SENHA = 12
TAMANHO_MINIMO_TOKEN_INTEGRACAO = 32


class Settings(BaseSettings):
    app_name: str = "INPI API"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://inpi:inpi@localhost:5432/inpi"
    app_db_password: str | None = None
    admin_username: str = "admin"
    admin_password: str = "altere-esta-senha"
    admin_email: str = "admin@zeregistra.local"
    session_duration_hours: int = 8
    session_idle_minutes: int = 60
    alto_renome_page_url: str = "https://www.gov.br/inpi/pt-br/servicos/marcas/alto-renome/"
    audit_ip_salt: str = "desenvolvimento-local"
    security_master_key: str = "desenvolvimento-local-chave-mestra"
    security_master_key_version: int = 1
    security_master_key_previous: str = ""
    security_master_key_previous_version: int = 0
    admin_force_https: bool = False
    integration_auth_enabled: bool = False
    inpi_integration_token: str = ""
    rpi_sync_interval_seconds: int = 21_600
    rpi_sync_start_number: int = 2900
    rpi_sync_poll_seconds: int = 10
    rpi_stale_hours: float = 12.0
    rpi_minimum_record_ratio: float = 0.50
    rpi_anomaly_reference_minimum: int = 1_000
    default_organization_slug: str = "ze-registra"
    default_organization_id: int = 1
    cors_allowed_origins: str = "*"
    redis_url: str = "redis://localhost:6379/0"
    redis_required: bool = False
    queue_retry_base_seconds: int = 5
    queue_retry_max_seconds: int = 300
    queue_idempotency_ttl_seconds: int = 86_400
    # Usa Redis para o rate limiting (necessário com múltiplos workers/instâncias).
    # Desligado por padrão: em processo único a janela em memória basta.
    ratelimit_redis_enabled: bool = False
    trusted_proxy_networks: str = "127.0.0.1/32,::1/128"
    password_reset_minutes: int = 30
    app_public_url: str = "http://localhost:8000"
    email_enabled: bool = False
    email_from_address: str = "nao-responda@zeregistra.local"
    email_from_name: str = "Zé Registra"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    smtp_timeout_seconds: float = 10.0
    smtp_max_attempts: int = 3
    oauth_attempt_minutes: int = 10
    oauth_auto_link_verified_email: bool = True
    google_oauth_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    apple_oauth_enabled: bool = False
    apple_client_id: str = ""
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key: str = ""
    gateway_webhook_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        origens = [origem.strip() for origem in self.cors_allowed_origins.split(",")]
        return [origem for origem in origens if origem]

    @property
    def trusted_proxy_cidrs(self) -> list[str]:
        return [item.strip() for item in self.trusted_proxy_networks.split(",") if item.strip()]

    @model_validator(mode="after")
    def exigir_senha_forte_em_producao(self) -> "Settings":
        if self.google_oauth_enabled and not (self.google_client_id and self.google_client_secret):
            raise ValueError(
                "GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET sao obrigatorios "
                "quando o Google OAuth estiver ativo."
            )
        if self.apple_oauth_enabled and not all(
            (self.apple_client_id, self.apple_team_id, self.apple_key_id, self.apple_private_key)
        ):
            raise ValueError(
                "APPLE_CLIENT_ID, APPLE_TEAM_ID, APPLE_KEY_ID e APPLE_PRIVATE_KEY "
                "sao obrigatorios quando o Apple OAuth estiver ativo."
            )
        if self.apple_oauth_enabled and (
            not self.app_public_url.startswith("https://") or "localhost" in self.app_public_url
        ):
            raise ValueError("Sign in with Apple exige APP_PUBLIC_URL HTTPS com dominio real.")
        if (
            self.integration_auth_enabled
            and len(self.inpi_integration_token) < TAMANHO_MINIMO_TOKEN_INTEGRACAO
        ):
            raise ValueError(
                "INPI_INTEGRATION_TOKEN deve ter ao menos "
                f"{TAMANHO_MINIMO_TOKEN_INTEGRACAO} caracteres quando a autenticação estiver ativa."
            )
        if self.app_env.lower() != "production":
            return self
        if self.admin_password in SENHAS_PLACEHOLDER:
            raise ValueError(
                "ADMIN_PASSWORD não pode usar o valor padrão em produção. "
                "Defina uma senha administrativa própria."
            )
        if len(self.admin_password) < TAMANHO_MINIMO_SENHA:
            raise ValueError(
                f"ADMIN_PASSWORD deve ter ao menos {TAMANHO_MINIMO_SENHA} caracteres em produção."
            )
        if len(self.audit_ip_salt) < 16:
            raise ValueError("AUDIT_IP_SALT deve ter ao menos 16 caracteres em produção.")
        if len(self.security_master_key) < 32:
            raise ValueError("SECURITY_MASTER_KEY deve ter ao menos 32 caracteres em produção.")
        if self.security_master_key_version < 1:
            raise ValueError("SECURITY_MASTER_KEY_VERSION deve ser positivo.")
        if self.security_master_key_previous and (
            self.security_master_key_previous_version < 1
            or self.security_master_key_previous_version == self.security_master_key_version
        ):
            raise ValueError(
                "SECURITY_MASTER_KEY_PREVIOUS_VERSION deve identificar uma versao anterior."
            )
        if "inpi:inpi@" in self.database_url or "change-me" in self.database_url:
            raise ValueError("DATABASE_URL usa credenciais padrão em produção.")
        if not self.admin_force_https:
            raise ValueError("ADMIN_FORCE_HTTPS deve estar ativo em produção.")
        if not self.integration_auth_enabled:
            raise ValueError("INTEGRATION_AUTH_ENABLED deve estar ativo em produção.")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ALLOWED_ORIGINS não pode usar curinga em produção.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

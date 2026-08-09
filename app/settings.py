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
    admin_force_https: bool = False
    integration_auth_enabled: bool = False
    inpi_integration_token: str = ""
    rpi_sync_interval_seconds: int = 21_600
    rpi_sync_start_number: int = 2900
    rpi_sync_poll_seconds: int = 10
    default_organization_slug: str = "ze-registra"
    default_organization_id: int = 1
    cors_allowed_origins: str = "*"
    redis_url: str = "redis://localhost:6379/0"
    redis_required: bool = False
    password_reset_minutes: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        origens = [origem.strip() for origem in self.cors_allowed_origins.split(",")]
        return [origem for origem in origens if origem]

    @model_validator(mode="after")
    def exigir_senha_forte_em_producao(self) -> "Settings":
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

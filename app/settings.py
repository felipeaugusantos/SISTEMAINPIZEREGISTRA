from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SENHAS_PLACEHOLDER = frozenset({"", "altere-esta-senha", "defina-uma-senha-forte"})
TAMANHO_MINIMO_SENHA = 8


class Settings(BaseSettings):
    app_name: str = "INPI API"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://inpi:inpi@localhost:5432/inpi"
    admin_username: str = "admin"
    admin_password: str = "altere-esta-senha"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def exigir_senha_forte_em_producao(self) -> "Settings":
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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

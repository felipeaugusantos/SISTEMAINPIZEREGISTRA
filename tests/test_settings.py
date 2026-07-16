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
    settings = Settings(app_env="production", admin_password="Uma-Senha-Bem-Forte-2026")
    assert settings.app_env == "production"


def test_desenvolvimento_nao_valida_senha() -> None:
    settings = Settings(app_env="development", admin_password="")
    assert settings.admin_password == ""

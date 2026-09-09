"""Fase 0 da auditoria (03/09/2026) -- endurecimento de producao.

Achado F0-1/F0-2: docker/deploy.sh chamava `docker compose` sem `-f
compose.yaml -f compose.production.yaml`, e nao havia `COMPOSE_FILE` em
nenhum lugar do ambiente da VPS. Resultado: compose.production.yaml nunca
era mesclado -- confirmado em producao via `docker compose config`
(mesma invocacao do deploy.sh) resolvendo APP_ENV=development,
ADMIN_FORCE_HTTPS=false, INTEGRATION_AUTH_ENABLED=false. Como
`Settings.exigir_senha_forte_em_producao` (app/settings.py) so valida
quando app_env=="production", nenhuma das checagens de producao (senha
forte, HTTPS, integracao, CORS) rodava de fato.

Os testes daqui nao usam segredos reais (regra 10) nem tocam banco de
producao (regra 11) -- so validam a composicao efetiva do Docker Compose
com valores de teste descartaveis.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
_DEPLOY_SH_PATH = RAIZ / "docker" / "deploy.sh"

if not _DEPLOY_SH_PATH.is_file():
    # Algumas imagens de teste (ex.: o servico `test` do compose, que so
    # copia app/migrations/tests/alembic.ini) nao incluem docker/ nem os
    # arquivos compose -- so o CI (checkout completo do repo) e uma execucao
    # local a partir da raiz do repositorio tem esses arquivos disponiveis.
    pytest.skip("docker/deploy.sh nao presente neste ambiente (imagem de teste minima)", allow_module_level=True)

DEPLOY_SH = _DEPLOY_SH_PATH.read_text(encoding="utf-8")
ROLLBACK_SH = (RAIZ / "docker" / "rollback.sh").read_text(encoding="utf-8")

# --- Checagens estaticas (nao precisam de Docker, sempre rodam no CI) ---


def test_deploy_script_define_overlay_de_producao():
    assert "COMPOSE=\"docker compose -f compose.yaml -f compose.production.yaml\"" in DEPLOY_SH


def test_deploy_script_nao_chama_docker_compose_sem_o_overlay():
    """Toda chamada a `docker compose` no script deve usar a variavel $COMPOSE
    (que carrega o overlay de producao), nao `docker compose` cru -- exceto na
    propria definicao da variavel."""
    chamadas_cruas = [
        linha
        for linha in DEPLOY_SH.splitlines()
        if "docker compose" in linha and not linha.strip().startswith("#") and "COMPOSE=" not in linha
    ]
    assert chamadas_cruas == [], f"chamadas a 'docker compose' sem usar $COMPOSE: {chamadas_cruas}"


def test_deploy_script_falha_se_ambiente_efetivo_nao_for_production():
    assert 'AMBIENTE_EFETIVO" != "production"' in DEPLOY_SH
    assert "exit 1" in DEPLOY_SH


def test_deploy_script_healthcheck_final_nao_engole_falha():
    """Regressao do padrao 'curl ... || true' (Fase 0, item 11): o script nao
    pode reportar sucesso sem checar de verdade se a api ficou saudavel."""
    trecho_final = DEPLOY_SH.split("verificando saude da api")[-1]
    assert "|| true" not in trecho_final
    assert 'SAUDAVEL" -ne 1' in trecho_final
    assert "exit 1" in trecho_final


def test_deploy_script_falha_se_commits_divergirem_entre_servicos():
    assert "SHAS_DIVERGENTES" in DEPLOY_SH
    assert "org.opencontainers.image.revision" in DEPLOY_SH


def test_deploy_script_valida_backup_antes_da_migration_quando_pedido():
    """Fase 9 (ordem recomendada de deploy), item 1: "backup validado" --
    VALIDAR_BACKUP=1 roda o simulado de restauracao (docker/simulado-
    restauracao.sh) logo apos o backup, antes de aplicar a migration.
    Opcional (nao muda o comportamento padrao de deploys sem migration
    pendente ou sem a flag) porque o simulado e um pg_restore completo."""
    assert "VALIDAR_BACKUP" in DEPLOY_SH
    indice_backup = DEPLOY_SH.index("./docker/backup-banco.sh")
    indice_validacao = DEPLOY_SH.index("./docker/simulado-restauracao.sh")
    indice_migration = DEPLOY_SH.index('echo "==> aplicando migrations"')
    assert indice_backup < indice_validacao < indice_migration


def test_rollback_script_define_overlay_de_producao():
    assert "COMPOSE=\"docker compose -f compose.yaml -f compose.production.yaml\"" in ROLLBACK_SH


def test_rollback_script_nao_chama_docker_compose_sem_o_overlay():
    chamadas_cruas = [
        linha
        for linha in ROLLBACK_SH.splitlines()
        if "docker compose" in linha and not linha.strip().startswith("#") and "COMPOSE=" not in linha
    ]
    assert chamadas_cruas == [], f"chamadas a 'docker compose' sem usar $COMPOSE: {chamadas_cruas}"


def test_rollback_script_healthcheck_nao_engole_falha():
    assert "|| true" not in ROLLBACK_SH
    assert "exit 1" in ROLLBACK_SH


def test_dockerfile_aceita_git_sha_como_label_oci():
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    assert 'ARG GIT_SHA' in dockerfile
    assert 'LABEL org.opencontainers.image.revision="${GIT_SHA}"' in dockerfile


def test_deploy_e_rollback_informam_versao_real_ao_container():
    compose = (RAIZ / "compose.yaml").read_text(encoding="utf-8")

    assert 'export APP_VERSION="$TAG_VERSAO"' in DEPLOY_SH
    assert 'export APP_VERSION="$VERSAO"' in ROLLBACK_SH
    assert compose.count("APP_VERSION: ${APP_VERSION:-development}") == 3


# --- Checagens de composicao efetiva (precisam de Docker -- puladas se indisponivel) ---

_ENV_TESTE_PRODUCAO = {
    "UVICORN_FORWARDED_ALLOW_IPS": "172.28.0.1",
    "CORS_ALLOWED_ORIGINS": "https://teste.exemplo.com.br",
    "HEALTH_API_KEY": "chave-de-teste-descartavel-nao-e-segredo-real",
    "SECURITY_MASTER_KEY": "chave-mestra-de-teste-com-mais-de-trinta-e-dois-caracteres",
    "AUDIT_IP_SALT": "sal-de-teste-com-mais-de-dezesseis-caracteres",
    "ADMIN_PASSWORD": "Senha-De-Teste-Descartavel-123!",
    "INPI_INTEGRATION_TOKEN": "token-de-teste-com-mais-de-trinta-e-dois-caracteres-1234",
}

pytestmark_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker indisponivel neste ambiente")


def _compose_config(*, com_overlay_producao: bool) -> str:
    args = ["docker", "compose", "-f", "compose.yaml"]
    if com_overlay_producao:
        args += ["-f", "compose.production.yaml"]
    args += ["config"]
    ambiente = {**os.environ, **_ENV_TESTE_PRODUCAO}
    resultado = subprocess.run(args, cwd=RAIZ, env=ambiente, capture_output=True, text=True, timeout=30, check=True)
    return resultado.stdout


def _valor_apos(config: str, chave: str, apos_servico: str) -> str | None:
    bloco = config.split(f"\n  {apos_servico}:", 1)
    if len(bloco) < 2:
        return None
    proximo_servico = re.search(r"\n  \w[\w-]*:\n", bloco[1])
    trecho = bloco[1][: proximo_servico.start()] if proximo_servico else bloco[1]
    achado = re.search(rf"^\s*{chave}:\s*\"?([^\"\n]*)\"?\s*$", trecho, re.MULTILINE)
    return achado.group(1) if achado else None


@pytestmark_docker
@pytest.mark.parametrize("servico", ["api", "worker", "rpi-sync"])
def test_overlay_de_producao_resolve_app_env_production(servico):
    config = _compose_config(com_overlay_producao=True)
    assert _valor_apos(config, "APP_ENV", servico) == "production"


@pytestmark_docker
@pytest.mark.parametrize("servico", ["api", "worker", "rpi-sync"])
def test_overlay_de_producao_habilita_seguranca(servico):
    config = _compose_config(com_overlay_producao=True)
    assert _valor_apos(config, "ADMIN_FORCE_HTTPS", servico) == "true"
    assert _valor_apos(config, "INTEGRATION_AUTH_ENABLED", servico) == "true"
    assert _valor_apos(config, "BOOTSTRAP_ADMIN", servico) == "false"


@pytestmark_docker
def test_overlay_de_producao_cors_nao_e_curinga():
    config = _compose_config(com_overlay_producao=True)
    assert _valor_apos(config, "CORS_ALLOWED_ORIGINS", "api") != "*"


@pytestmark_docker
def test_sem_overlay_de_producao_ambiente_fica_development():
    """Documenta o comportamento que causou o achado F0-1: SEM os dois `-f`,
    o compose sozinho resolve para o ambiente de desenvolvimento -- e por
    isso deploy.sh precisa sempre incluir o overlay explicitamente."""
    config = _compose_config(com_overlay_producao=False)
    assert _valor_apos(config, "APP_ENV", "api") == "development"

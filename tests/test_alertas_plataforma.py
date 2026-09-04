from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.alertas_plataforma import (
    registrar_alerta_plataforma,
    resolver_alerta_plataforma,
    verificar_saude_plataforma,
)
from app.models import AlertaSistema, RpiSyncEstado
from tests.conftest import FakeResult, FakeSession

# --- Achado FASE6-9 da auditoria (04/09/2026): fila de falhas, RPI
# desatualizada e latência/erro de API viravam só um número num painel --
# ninguém era avisado proativamente. ---


async def _sem_email(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture(autouse=True)
def _mock_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.alertas_plataforma.enviar_alerta_plataforma", _sem_email)


async def test_registrar_alerta_plataforma_cria_quando_nao_existe() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    await registrar_alerta_plataforma(session, codigo="X", severidade="aviso", mensagem="teste")
    alertas = [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    assert len(alertas) == 1
    assert alertas[0].organizacao_id is None
    assert alertas[0].codigo == "X"


async def test_registrar_alerta_plataforma_nao_duplica_quando_ja_aberto() -> None:
    session = FakeSession([FakeResult(scalar=99)])
    await registrar_alerta_plataforma(session, codigo="X", severidade="aviso", mensagem="teste")
    assert session.adicionados == []


async def test_resolver_alerta_plataforma_marca_resolvido() -> None:
    alerta = AlertaSistema(
        id=1, organizacao_id=None, severidade="aviso", codigo="X", mensagem="m", detalhes={},
        resolvido_em=None, criado_em=datetime(2026, 9, 4, tzinfo=UTC),
    )
    session = FakeSession([FakeResult(itens=[alerta])])
    await resolver_alerta_plataforma(session, codigo="X")
    assert alerta.resolvido_em is not None


def _fila_ok(falhas: int = 0):
    async def _chamada() -> dict:
        return {
            "status": "ok", "pendentes": 0, "falhas": falhas, "processando": 0,
            "retries_aguardando": 0, "metricas": {},
        }

    return _chamada


async def _fila_indisponivel() -> dict:
    return {"status": "indisponivel", "erro": "ConnectionError"}


def _estado_rpi_ok() -> RpiSyncEstado:
    return RpiSyncEstado(id=1, status="ok", ultima_rpi_oficial=2900, ultima_verificacao_em=datetime.now(UTC))


def _ultima_rpi_ok():
    return SimpleNamespace(numero_rpi=2900, importado_em=datetime.now(UTC), status_integridade="ok")


async def test_verificar_saude_plataforma_tudo_ok_nao_cria_alerta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.alertas_plataforma.status_fila", _fila_ok(falhas=0))
    session = FakeSession(
        [
            FakeResult(itens=[]),  # resolver FILA_INDISPONIVEL
            FakeResult(itens=[]),  # resolver FILA_FALHAS_ALTA
            FakeResult(scalar=_ultima_rpi_ok()),  # select RpiImportacao
            FakeResult(itens=[]),  # resolver RPI_DESATUALIZADA (status "ok")
            FakeResult(itens=[(0, 0, 0.0)]),  # métricas API (0 requisições)
            FakeResult(itens=[]),  # resolver API_LATENCIA_ERRO_ALTA
        ],
        objetos_get=[_estado_rpi_ok()],
    )
    await verificar_saude_plataforma(session)
    assert [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)] == []


async def test_verificar_saude_plataforma_fila_indisponivel_cria_alerta_critico(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.alertas_plataforma.status_fila", _fila_indisponivel)
    session = FakeSession(
        [
            FakeResult(scalar=None),  # registrar FILA_INDISPONIVEL (dedup check)
            FakeResult(scalar=None),  # select RpiImportacao -> nenhuma ainda
            FakeResult(scalar=None),  # registrar RPI_DESATUALIZADA (sem dado -> "erro", dedup check)
            FakeResult(itens=[(0, 0, 0.0)]),  # métricas API
            FakeResult(itens=[]),  # resolver API_LATENCIA_ERRO_ALTA
        ],
        objetos_get=[None],
    )
    await verificar_saude_plataforma(session)
    alertas = {obj.codigo: obj for obj in session.adicionados if isinstance(obj, AlertaSistema)}
    assert "FILA_INDISPONIVEL" in alertas
    assert alertas["FILA_INDISPONIVEL"].severidade == "critico"


async def test_verificar_saude_plataforma_falhas_acima_do_limite_cria_alerta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.alertas_plataforma.status_fila", _fila_ok(falhas=10))
    session = FakeSession(
        [
            FakeResult(itens=[]),  # resolver FILA_INDISPONIVEL
            FakeResult(scalar=None),  # registrar FILA_FALHAS_ALTA (dedup check)
            FakeResult(scalar=_ultima_rpi_ok()),  # select RpiImportacao
            FakeResult(itens=[]),  # resolver RPI_DESATUALIZADA
            FakeResult(itens=[(0, 0, 0.0)]),  # métricas API
            FakeResult(itens=[]),  # resolver API_LATENCIA_ERRO_ALTA
        ],
        objetos_get=[_estado_rpi_ok()],
    )
    await verificar_saude_plataforma(session)
    alertas = {obj.codigo: obj for obj in session.adicionados if isinstance(obj, AlertaSistema)}
    assert "FILA_FALHAS_ALTA" in alertas
    assert alertas["FILA_FALHAS_ALTA"].detalhes["falhas"] == 10


async def test_verificar_saude_plataforma_api_com_amostra_pequena_nao_alerta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesmo com taxa de erro/latência ruins, uma amostra pequena (<20
    requisições em 24h) não deve gerar alerta -- evita falso positivo em
    ambiente de baixo tráfego."""
    monkeypatch.setattr("app.alertas_plataforma.status_fila", _fila_ok(falhas=0))
    session = FakeSession(
        [
            FakeResult(itens=[]),  # resolver FILA_INDISPONIVEL
            FakeResult(itens=[]),  # resolver FILA_FALHAS_ALTA
            FakeResult(scalar=_ultima_rpi_ok()),  # select RpiImportacao
            FakeResult(itens=[]),  # resolver RPI_DESATUALIZADA
            FakeResult(itens=[(5, 5, 9999.0)]),  # 5 requisições, 100% erro, latência altíssima
            FakeResult(itens=[]),  # resolver API_LATENCIA_ERRO_ALTA
        ],
        objetos_get=[_estado_rpi_ok()],
    )
    await verificar_saude_plataforma(session)
    codigos_alertados = [obj.codigo for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    assert "API_LATENCIA_ERRO_ALTA" not in codigos_alertados


def _settings_producao(tmp_path, **overrides: object) -> SimpleNamespace:
    base = {
        "alerta_fila_falhas_limite": 5,
        "rpi_stale_hours": 12.0,
        "app_env": "production",
        "backups_dir": str(tmp_path),
        "alerta_backup_max_horas": 26.0,
        "alerta_api_taxa_erro_limite": 0.05,
        "alerta_api_latencia_media_ms_limite": 2000.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_verificar_saude_plataforma_producao_sem_backup_alerta_critico(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr("app.alertas_plataforma.status_fila", _fila_ok(falhas=0))
    monkeypatch.setattr("app.alertas_plataforma.get_settings", lambda: _settings_producao(tmp_path))
    session = FakeSession(
        [
            FakeResult(itens=[]),  # resolver FILA_INDISPONIVEL
            FakeResult(itens=[]),  # resolver FILA_FALHAS_ALTA
            FakeResult(scalar=_ultima_rpi_ok()),  # select RpiImportacao
            FakeResult(itens=[]),  # resolver RPI_DESATUALIZADA
            FakeResult(scalar=None),  # registrar BACKUP_AUSENTE (dedup check) -- tmp_path vazio, sem dump
            FakeResult(itens=[(0, 0, 0.0)]),  # métricas API
            FakeResult(itens=[]),  # resolver API_LATENCIA_ERRO_ALTA
        ],
        objetos_get=[_estado_rpi_ok()],
    )
    await verificar_saude_plataforma(session)
    alertas = {obj.codigo: obj for obj in session.adicionados if isinstance(obj, AlertaSistema)}
    assert "BACKUP_AUSENTE" in alertas
    assert alertas["BACKUP_AUSENTE"].severidade == "critico"


async def test_verificar_saude_plataforma_producao_backup_recente_nao_alerta(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / "inpi-20260904-100000.dump").write_bytes(b"conteudo")
    monkeypatch.setattr("app.alertas_plataforma.status_fila", _fila_ok(falhas=0))
    monkeypatch.setattr("app.alertas_plataforma.get_settings", lambda: _settings_producao(tmp_path))
    session = FakeSession(
        [
            FakeResult(itens=[]),  # resolver FILA_INDISPONIVEL
            FakeResult(itens=[]),  # resolver FILA_FALHAS_ALTA
            FakeResult(scalar=_ultima_rpi_ok()),  # select RpiImportacao
            FakeResult(itens=[]),  # resolver RPI_DESATUALIZADA
            FakeResult(itens=[]),  # resolver BACKUP_AUSENTE -- dump recente
            FakeResult(itens=[(0, 0, 0.0)]),  # métricas API
            FakeResult(itens=[]),  # resolver API_LATENCIA_ERRO_ALTA
        ],
        objetos_get=[_estado_rpi_ok()],
    )
    await verificar_saude_plataforma(session)
    codigos_alertados = [obj.codigo for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    assert "BACKUP_AUSENTE" not in codigos_alertados


async def test_verificar_saude_plataforma_producao_backup_antigo_alerta(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import os
    import time

    antigo = tmp_path / "inpi-20260101-000000.dump"
    antigo.write_bytes(b"conteudo")
    os.utime(antigo, (time.time() - 30 * 3600, time.time() - 30 * 3600))  # 30h atrás
    monkeypatch.setattr("app.alertas_plataforma.status_fila", _fila_ok(falhas=0))
    monkeypatch.setattr("app.alertas_plataforma.get_settings", lambda: _settings_producao(tmp_path))
    session = FakeSession(
        [
            FakeResult(itens=[]),  # resolver FILA_INDISPONIVEL
            FakeResult(itens=[]),  # resolver FILA_FALHAS_ALTA
            FakeResult(scalar=_ultima_rpi_ok()),  # select RpiImportacao
            FakeResult(itens=[]),  # resolver RPI_DESATUALIZADA
            FakeResult(scalar=None),  # registrar BACKUP_AUSENTE (dedup check) -- dump velho
            FakeResult(itens=[(0, 0, 0.0)]),  # métricas API
            FakeResult(itens=[]),  # resolver API_LATENCIA_ERRO_ALTA
        ],
        objetos_get=[_estado_rpi_ok()],
    )
    await verificar_saude_plataforma(session)
    alertas = {obj.codigo: obj for obj in session.adicionados if isinstance(obj, AlertaSistema)}
    assert "BACKUP_AUSENTE" in alertas


async def test_verificar_saude_plataforma_dev_nao_checa_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Fora de produção, backups/ não é montado -- não deve nem tentar checar
    (evita alerta falso/erro em dev e nos próprios testes)."""
    monkeypatch.setattr("app.alertas_plataforma.status_fila", _fila_ok(falhas=0))
    monkeypatch.setattr(
        "app.alertas_plataforma.get_settings", lambda: _settings_producao(tmp_path, app_env="development")
    )
    session = FakeSession(
        [
            FakeResult(itens=[]),  # resolver FILA_INDISPONIVEL
            FakeResult(itens=[]),  # resolver FILA_FALHAS_ALTA
            FakeResult(scalar=_ultima_rpi_ok()),  # select RpiImportacao
            FakeResult(itens=[]),  # resolver RPI_DESATUALIZADA
            FakeResult(itens=[(0, 0, 0.0)]),  # métricas API (sem checagem de backup no meio)
            FakeResult(itens=[]),  # resolver API_LATENCIA_ERRO_ALTA
        ],
        objetos_get=[_estado_rpi_ok()],
    )
    await verificar_saude_plataforma(session)
    codigos_alertados = [obj.codigo for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    assert "BACKUP_AUSENTE" not in codigos_alertados


async def test_verificar_saude_plataforma_api_com_amostra_grande_e_erro_alto_alerta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.alertas_plataforma.status_fila", _fila_ok(falhas=0))
    session = FakeSession(
        [
            FakeResult(itens=[]),  # resolver FILA_INDISPONIVEL
            FakeResult(itens=[]),  # resolver FILA_FALHAS_ALTA
            FakeResult(scalar=_ultima_rpi_ok()),  # select RpiImportacao
            FakeResult(itens=[]),  # resolver RPI_DESATUALIZADA
            FakeResult(itens=[(100, 20, 500.0)]),  # 100 requisições, 20% erro (> limite de 5%)
            FakeResult(scalar=None),  # registrar API_LATENCIA_ERRO_ALTA (dedup check)
        ],
        objetos_get=[_estado_rpi_ok()],
    )
    await verificar_saude_plataforma(session)
    alertas = {obj.codigo: obj for obj in session.adicionados if isinstance(obj, AlertaSistema)}
    assert "API_LATENCIA_ERRO_ALTA" in alertas

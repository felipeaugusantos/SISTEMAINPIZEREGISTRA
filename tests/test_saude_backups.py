import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.alertas_plataforma import _verificar_rotina_backups
from app.auth import obter_usuario_atual
from app.main import app
from app.models import AlertaSistema
from app.saude_backups import coletar_saude_backups
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

AGORA = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


def _evento(etapa: str, horas_atras: float, ok: bool = True, tamanho: int = 0) -> str:
    return json.dumps(
        {
            "etapa": etapa,
            "ok": ok,
            "em": (AGORA - timedelta(hours=horas_atras)).isoformat(),
            "arquivo": "inpi-x.dump",
            "tamanho_bytes": tamanho,
            "duracao_s": 5,
        }
    )


def _arquivo(caminho: Path, horas_atras: float, conteudo: bytes = b"x") -> None:
    caminho.write_bytes(conteudo)
    instante = (AGORA - timedelta(hours=horas_atras)).timestamp()
    os.utime(caminho, (instante, instante))


def _historico(diretorio: Path, *linhas: str) -> None:
    (diretorio / "historico-rotina.jsonl").write_text("\n".join(linhas) + "\n", encoding="utf-8")


def _etapas(resultado: dict) -> dict:
    return {etapa["chave"]: etapa for etapa in resultado["etapas"]}


def test_diretorio_vazio_e_critico_no_dump(tmp_path: Path) -> None:
    resultado = coletar_saude_backups(tmp_path, agora=AGORA)
    etapas = _etapas(resultado)
    assert etapas["dump"]["status"] == "critico"
    assert etapas["offsite"]["status"] == "atencao"
    assert etapas["simulado"]["status"] == "atencao"
    assert etapas["copia_pc"]["status"] == "desconhecido"
    assert resultado["geral"] == "critico"


def test_rotina_saudavel_fica_ok(tmp_path: Path) -> None:
    _arquivo(tmp_path / "inpi-20261014-020000.dump", 10, b"d" * 100)
    _arquivo(tmp_path / ".pc-inpi-20261014-020000.dump", 8)
    _historico(
        tmp_path,
        _evento("dump", 10, tamanho=100),
        _evento("offsite", 9, tamanho=100),
        _evento("simulado", 30),
    )
    etapas = _etapas(coletar_saude_backups(tmp_path, agora=AGORA, caminho_disco=str(tmp_path)))
    assert etapas["dump"]["status"] == "ok"
    assert etapas["dump"]["tamanho_bytes"] == 100
    assert etapas["offsite"]["status"] == "ok"
    assert etapas["simulado"]["status"] == "ok"
    assert etapas["copia_pc"]["status"] == "ok"


def test_dump_antigo_e_critico(tmp_path: Path) -> None:
    _arquivo(tmp_path / "inpi-20261012-020000.dump", 60)
    _historico(tmp_path, _evento("dump", 60))
    assert _etapas(coletar_saude_backups(tmp_path, agora=AGORA))["dump"]["status"] == "critico"


def test_ultima_execucao_do_dump_falhou(tmp_path: Path) -> None:
    _arquivo(tmp_path / "inpi-20261013-020000.dump", 20)
    _historico(tmp_path, _evento("dump", 20), _evento("dump", 2, ok=False))
    etapa = _etapas(coletar_saude_backups(tmp_path, agora=AGORA))["dump"]
    assert etapa["status"] == "critico"
    assert "falhou" in etapa["detalhe"]


def test_queda_brusca_de_tamanho_gera_atencao(tmp_path: Path) -> None:
    _arquivo(tmp_path / "inpi-20261014-020000.dump", 10, b"d" * 10)
    _historico(tmp_path, _evento("dump", 34, tamanho=1000), _evento("dump", 10, tamanho=600))
    assert _etapas(coletar_saude_backups(tmp_path, agora=AGORA))["dump"]["status"] == "atencao"


def test_envio_externo_atrasado_e_critico(tmp_path: Path) -> None:
    _arquivo(tmp_path / "inpi-20261014-020000.dump", 10)
    _historico(tmp_path, _evento("dump", 10), _evento("offsite", 40))
    assert _etapas(coletar_saude_backups(tmp_path, agora=AGORA))["offsite"]["status"] == "critico"


def test_envio_externo_falhou(tmp_path: Path) -> None:
    _arquivo(tmp_path / "inpi-20261014-020000.dump", 10)
    _historico(tmp_path, _evento("offsite", 30), _evento("offsite", 8, ok=False))
    assert _etapas(coletar_saude_backups(tmp_path, agora=AGORA))["offsite"]["status"] == "critico"


def test_marcador_offsite_vale_sem_historico(tmp_path: Path) -> None:
    _arquivo(tmp_path / "inpi-20261014-020000.dump", 10)
    _arquivo(tmp_path / ".offsite-inpi-20261014-020000.dump", 9)
    assert _etapas(coletar_saude_backups(tmp_path, agora=AGORA))["offsite"]["status"] == "ok"


def test_simulado_falho_ou_vencido(tmp_path: Path) -> None:
    _historico(tmp_path, _evento("simulado", 3, ok=False))
    assert _etapas(coletar_saude_backups(tmp_path, agora=AGORA))["simulado"]["status"] == "critico"
    _historico(tmp_path, _evento("simulado", 24 * 12))
    assert _etapas(coletar_saude_backups(tmp_path, agora=AGORA))["simulado"]["status"] == "critico"


def test_copia_no_pc_vencida(tmp_path: Path) -> None:
    _arquivo(tmp_path / ".pc-inpi-20261010-020000.dump", 24 * 5)
    assert _etapas(coletar_saude_backups(tmp_path, agora=AGORA))["copia_pc"]["status"] == "critico"


def test_historico_ignora_linhas_invalidas_e_agrupa_por_dia(tmp_path: Path) -> None:
    _historico(tmp_path, "lixo", '{"etapa":"x"}', _evento("dump", 10), _evento("offsite", 9, ok=False))
    resultado = coletar_saude_backups(tmp_path, agora=AGORA)
    assert resultado["historico"] == [{"data": "2026-10-14", "dump": True, "offsite": False, "simulado": None}]


def test_endpoint_exclusivo_do_superadmin(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _snapshot() -> dict:
        return {"coletado_em": AGORA.isoformat(), "geral": "ok", "etapas": [], "historico": []}

    monkeypatch.setattr("app.api.observabilidade.ler_snapshot", _snapshot)
    cliente = TestClient(app)

    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario_teste())
    assert cliente.get("/v1/admin/observabilidade/saude-backups").status_code == 403

    app.dependency_overrides[obter_usuario_atual] = auth_override(replace(usuario_teste(), superadmin=True))
    resposta = cliente.get("/v1/admin/observabilidade/saude-backups")
    assert resposta.status_code == 200
    assert resposta.json()["disponivel"] is True


def test_endpoint_sem_leitura_ainda(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _vazio() -> None:
        return None

    monkeypatch.setattr("app.api.observabilidade.ler_snapshot", _vazio)
    app.dependency_overrides[obter_usuario_atual] = auth_override(replace(usuario_teste(), superadmin=True))
    corpo = TestClient(app).get("/v1/admin/observabilidade/saude-backups").json()
    assert corpo["disponivel"] is False


async def test_alerta_backup_rotina_abre_e_resolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    publicados: list[dict] = []

    async def _publicar(dados: dict) -> None:
        publicados.append(dados)

    async def _sem_email(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("app.alertas_plataforma.publicar_snapshot", _publicar)
    monkeypatch.setattr("app.alertas_plataforma.enviar_alerta_plataforma", _sem_email)

    # diretório vazio -> dump crítico -> alerta aberto
    session = FakeSession([FakeResult(scalar=None)])
    await _verificar_rotina_backups(session, tmp_path)
    alertas = [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    assert len(alertas) == 1
    assert alertas[0].codigo == "BACKUP_ROTINA"
    assert publicados and publicados[0]["geral"] == "critico"


async def test_falha_na_avaliacao_nao_propaga(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _quebra(*_a: object, **_k: object) -> dict:
        raise RuntimeError("falha simulada")

    monkeypatch.setattr("app.alertas_plataforma.coletar_saude_backups", _quebra)
    await _verificar_rotina_backups(FakeSession([]), tmp_path)

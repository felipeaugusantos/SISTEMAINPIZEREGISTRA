"""Importação automática do cache nacional a partir de arquivos enviados
para a VPS (pedido do usuário, 30/09/2026): a Receita recusa a VPS, os
arquivos chegam por envio agendado e o worker dispara a importação sozinho."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import prospeccao_cache_rfb
from app.models import ImportacaoCnpjRfb
from app.prospeccao_cache_rfb import (
    ARQUIVOS_NECESSARIOS,
    MARCADOR_ENVIO_COMPLETO,
    disparar_importacao_de_arquivos_enviados,
    periodos_enviados_completos,
)
from app.worker import TAREFAS_MANUTENCAO_HORARIA
from tests.conftest import FakeResult, FakeSession


def _periodo(raiz: Path, nome: str, *, marcador: bool = True, faltando: str | None = None) -> None:
    pasta = raiz / nome
    pasta.mkdir()
    for arquivo in ARQUIVOS_NECESSARIOS:
        if arquivo != faltando:
            (pasta / f"{arquivo}.zip").write_bytes(b"zip")
    if marcador:
        (pasta / MARCADOR_ENVIO_COMPLETO).write_text("ok")


def test_so_considera_periodos_com_marcador_e_todos_os_arquivos(tmp_path: Path) -> None:
    _periodo(tmp_path, "2026-08")
    _periodo(tmp_path, "2026-09")
    _periodo(tmp_path, "2026-10", marcador=False)
    _periodo(tmp_path, "2026-11", faltando="Estabelecimentos3")
    assert periodos_enviados_completos(str(tmp_path)) == ["2026-09", "2026-08"]
    assert periodos_enviados_completos(str(tmp_path / "inexistente")) == []


def _configurar(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> list[tuple]:
    enfileirados: list[tuple] = []

    async def _enfileirar(tipo: str, payload: dict, **_kwargs: object) -> dict:
        enfileirados.append((tipo, payload))
        return {"id": "job"}

    monkeypatch.setattr(prospeccao_cache_rfb, "enfileirar", _enfileirar)
    monkeypatch.setattr(
        prospeccao_cache_rfb, "get_settings", lambda: SimpleNamespace(rfb_cnpj_cache_dir=str(cache_dir))
    )
    return enfileirados


def test_dispara_o_periodo_mais_recente_ainda_nao_importado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _periodo(tmp_path, "2026-09")
    enfileirados = _configurar(monkeypatch, tmp_path)
    # sem execução em andamento, sem execução encerrada, 0 falhas
    session = FakeSession([FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=0)])

    execucao_id = asyncio.run(disparar_importacao_de_arquivos_enviados(session))

    assert execucao_id == 1
    assert session.commits == 1
    payload = {"execucao_id": 1, "periodo": "2026-09", "limite_linhas": None}
    assert enfileirados == [("prospeccao.importar_cnpj_rfb", payload)]


def test_nao_dispara_com_importacao_em_andamento(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _periodo(tmp_path, "2026-09")
    enfileirados = _configurar(monkeypatch, tmp_path)
    ativa = ImportacaoCnpjRfb(
        status="executando", etapa_atual="Carregando empresas 2/10", solicitado_em=datetime.now(UTC)
    )
    session = FakeSession([FakeResult(scalar=ativa)])

    assert asyncio.run(disparar_importacao_de_arquivos_enviados(session)) is None
    assert enfileirados == []


def test_libera_execucao_abandonada_e_dispara(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _periodo(tmp_path, "2026-09")
    enfileirados = _configurar(monkeypatch, tmp_path)
    abandonada = ImportacaoCnpjRfb(
        status="executando", etapa_atual=None, solicitado_em=datetime.now(UTC) - timedelta(hours=2)
    )
    session = FakeSession([FakeResult(scalar=abandonada), FakeResult(scalar=None), FakeResult(scalar=0)])

    assert asyncio.run(disparar_importacao_de_arquivos_enviados(session)) == 1
    assert abandonada.status == "erro"
    assert len(enfileirados) == 1


def test_nao_reimporta_periodo_concluido_ou_parado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _periodo(tmp_path, "2026-09")
    enfileirados = _configurar(monkeypatch, tmp_path)
    session = FakeSession([FakeResult(scalar=None), FakeResult(scalar=5)])

    assert asyncio.run(disparar_importacao_de_arquivos_enviados(session)) is None
    assert enfileirados == []


def test_desiste_apos_tres_falhas_no_mesmo_periodo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _periodo(tmp_path, "2026-09")
    enfileirados = _configurar(monkeypatch, tmp_path)
    session = FakeSession([FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=3)])

    assert asyncio.run(disparar_importacao_de_arquivos_enviados(session)) is None
    assert enfileirados == []


def test_rotina_roda_na_manutencao_horaria() -> None:
    assert "prospeccao.importar_cache_enviado" in TAREFAS_MANUTENCAO_HORARIA

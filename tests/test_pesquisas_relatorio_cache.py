import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import app.api.pesquisas as modulo_pesquisas
from app.api.pesquisas import obter_relatorio
from app.models import PesquisaMarca, VersaoRelatorioMarca
from tests.conftest import FakeResult, FakeSession

# --- Achado médio da Fase 12 (auditoria da Consulta de marcas, 22/09/2026):
# GET /v1/pesquisas-marca/{id}/relatorio recomputava o relatório inteiro
# (busca completa + risco + inferência de modelo) a cada chamada, mesmo com
# 60 requisições/minuto liberadas por IP na mesma rota -- custo de CPU/DB
# desproporcional. Corrigido servindo a última versão persistida quando
# recente o suficiente (mesmo padrão já usado em baixar_relatorio_pdf). ---


def _pesquisa() -> PesquisaMarca:
    return PesquisaMarca(
        id="11111111-1111-1111-1111-111111111111",
        organizacao_id=1,
        marca="ACME",
        atividade=None,
        tipo_pesquisa="completa",
        classe_nice=None,
    )


def _payload_minimo(marca: str = "ACME") -> dict:
    agora = datetime.now(UTC).isoformat()
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "versao": 1,
        "schema_versao": "relatorio-marca-4.4",
        "gerado_em": agora,
        "conteudo_hash": "abc",
        "marca": marca,
        "atividade": "Não informada",
        "tipo_pesquisa": "completa",
        "classe_nice": None,
        "criado_em": agora,
        "ultima_rpi": None,
        "classes_atividade": [],
        "matriz_afinidade_status": "pendente_de_validacao",
        "alto_renome_atualizado_em": None,
        "total": 0,
        "limite_exibido": 0,
        "itens": [],
    }


def test_relatorio_usa_versao_recente_sem_recalcular(monkeypatch: pytest.MonkeyPatch) -> None:
    pesquisa = _pesquisa()
    versao_recente = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=1,
        schema_versao="relatorio-marca-4.4",
        conteudo_hash="abc",
        payload=_payload_minimo(),
        gerado_em=datetime.now(UTC) - timedelta(seconds=5),
    )
    session = FakeSession([FakeResult(scalar=pesquisa), FakeResult(scalar=versao_recente)])

    async def _falhar_se_chamado(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("gerar_resumo_pesquisa não deveria rodar com cache recente")

    monkeypatch.setattr(modulo_pesquisas, "gerar_resumo_pesquisa", _falhar_se_chamado)

    organizacao = SimpleNamespace(id=1)
    resultado = asyncio.run(obter_relatorio(pesquisa.id, session, organizacao, None))

    assert resultado.marca == "ACME"


def test_relatorio_recalcula_quando_cache_expirou(monkeypatch: pytest.MonkeyPatch) -> None:
    pesquisa = _pesquisa()
    versao_antiga = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=1,
        schema_versao="relatorio-marca-4.4",
        conteudo_hash="abc",
        payload=_payload_minimo(),
        gerado_em=datetime.now(UTC) - timedelta(seconds=120),
    )
    session = FakeSession([FakeResult(scalar=pesquisa), FakeResult(scalar=versao_antiga)])

    chamadas: list[str] = []

    async def _sentinela(_session: object, _pesquisa: object) -> str:
        chamadas.append("chamou")
        return "resultado-recalculado"

    monkeypatch.setattr(modulo_pesquisas, "gerar_resumo_pesquisa", _sentinela)

    organizacao = SimpleNamespace(id=1)
    resultado = asyncio.run(obter_relatorio(pesquisa.id, session, organizacao, None))

    assert chamadas == ["chamou"]
    assert resultado == "resultado-recalculado"

from datetime import UTC, datetime

from app.models import ControleProducao
from app.production import (
    elegivel_rollout,
    ia_efetivamente_habilitada,
    versionar_relatorio,
)
from app.schemas import RelatorioMarcaResponse
from app.settings import Settings
from tests.conftest import FakeResult, FakeSession


def relatorio() -> RelatorioMarcaResponse:
    return RelatorioMarcaResponse(
        id="00000000-0000-0000-0000-000000000005",
        marca="SINAL TESTE",
        atividade="Serviços administrativos",
        tipo_pesquisa="completa",
        classe_nice=None,
        criado_em=datetime.now(UTC),
        ultima_rpi=2897,
        classes_atividade=[],
        matriz_afinidade_status="pendente_de_validacao",
        alto_renome_atualizado_em=None,
        total=0,
        limite_exibido=0,
        itens=[],
    )


def test_rollout_e_estavel_e_respeita_limites() -> None:
    chave = "pesquisa-estavel"

    assert elegivel_rollout(chave, 0) is False
    assert elegivel_rollout(chave, 100) is True
    assert elegivel_rollout(chave, 35) == elegivel_rollout(chave, 35)


def test_ia_exige_chave_mestra_controle_e_chave_api() -> None:
    controle = ControleProducao(id=1, ia_habilitada=True, ia_rollout_percentual=10)
    ativa = Settings(ai_explanations_enabled=True, openai_api_key="teste")
    mestre_desligada = Settings(ai_explanations_enabled=False, openai_api_key="teste")

    assert ia_efetivamente_habilitada(ativa, controle) is True
    assert ia_efetivamente_habilitada(mestre_desligada, controle) is False
    controle.ia_habilitada = False
    assert ia_efetivamente_habilitada(ativa, controle) is False


async def test_primeiro_snapshot_cria_versao_imutavel() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    resultado = await versionar_relatorio(session, relatorio())

    assert resultado.versao == 1
    assert resultado.schema_versao == "relatorio-marca-4.2"
    assert len(resultado.conteudo_hash) == 64
    assert resultado.gerado_em is not None
    assert session.adicionados[0].payload["conteudo_hash"] == resultado.conteudo_hash


async def test_conteudo_identico_reutiliza_snapshot_existente() -> None:
    primeira_sessao = FakeSession([FakeResult(scalar=None)])
    primeiro = await versionar_relatorio(primeira_sessao, relatorio())
    versao = primeira_sessao.adicionados[0]
    segunda_sessao = FakeSession([FakeResult(scalar=versao)])

    segundo = await versionar_relatorio(
        segunda_sessao,
        RelatorioMarcaResponse.model_validate(primeiro.model_dump()),
    )

    assert segundo.conteudo_hash == primeiro.conteudo_hash
    assert segundo.versao == 1
    assert segunda_sessao.adicionados == []

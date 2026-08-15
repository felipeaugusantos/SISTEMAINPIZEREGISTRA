from datetime import UTC, datetime

from app.models import PesquisaMarca, VersaoRelatorioMarca
from app.production import versionar_relatorio
from app.schemas import RelatorioMarcaResponse
from app.trademarks.analysis_workflow import EstadoAnalise
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


async def test_primeiro_snapshot_cria_versao_imutavel() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    resultado = await versionar_relatorio(session, relatorio())

    assert resultado.versao == 1
    assert resultado.schema_versao == "relatorio-marca-4.3"
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


async def test_nova_versao_reabre_revisao_e_invalida_emissao_anterior() -> None:
    pesquisa = PesquisaMarca(
        id=relatorio().id,
        organizacao_id=1,
        marca="SINAL TESTE",
        tipo_pesquisa="completa",
        analysis_state=EstadoAnalise.VALIDATED.value,
        validated_by="especialista@teste.local",
        validated_at=datetime.now(UTC),
        analysis_notes="Versão anterior validada.",
        relatorio_completo_gerado_em=datetime.now(UTC),
        relatorio_completo_gerado_por="comercial@teste.local",
    )
    anterior = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=1,
        schema_versao="relatorio-marca-4.3",
        conteudo_hash="hash-anterior",
        payload={},
    )

    class SessionComPesquisa(FakeSession):
        async def get(self, *_args, **_kwargs):
            return pesquisa

    session = SessionComPesquisa([FakeResult(scalar=anterior)])

    resultado = await versionar_relatorio(session, relatorio())

    assert resultado.versao == 2
    assert pesquisa.analysis_state == EstadoAnalise.PENDING_REVIEW.value
    assert pesquisa.validated_by is None
    assert pesquisa.validated_at is None
    assert pesquisa.relatorio_completo_gerado_em is None
    assert pesquisa.relatorio_completo_gerado_por is None

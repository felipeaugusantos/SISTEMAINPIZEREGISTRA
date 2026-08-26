from datetime import UTC, datetime

from app.models import ExecucaoAgenteRegistrabilidade, PesquisaMarca, VersaoRelatorioMarca
from app.trademarks.agent import analisar_registrabilidade, reprocessar_agentes_pendentes
from tests.conftest import FakeResult, FakeSession


def _matriz(*, cobertura: int = 100, impedimentos: int = 0, alertas: int = 0) -> dict:
    return {
        "cobertura_percentual": cobertura,
        "contagens": {
            "possivel_impedimento": impedimentos,
            "alerta": alertas,
            "nao_analisado": 0 if cobertura == 100 else 3,
        },
    }


def _previsao(probabilidade: float = 0.82, cobertura: float = 0.90) -> dict:
    return {
        "probabilidade_deferimento": probabilidade,
        "probabilidade_inferior": max(0, probabilidade - 0.10),
        "probabilidade_superior": min(1, probabilidade + 0.10),
        "confianca": 0.80,
        "cobertura_entrada": cobertura,
        "fatores_principais": [],
    }


def test_agente_se_abstem_quando_dados_sao_insuficientes() -> None:
    resultado = analisar_registrabilidade(
        matriz=_matriz(cobertura=40),
        pontuacao_risco=10,
        nivel_risco="baixo",
        previsao=_previsao(cobertura=0.90),
    )

    assert resultado.abstencao is True
    assert resultado.decisao == "dados_insuficientes"
    assert resultado.status == "revisao_humana"


def test_impedimento_oficial_prevalece_sobre_probabilidade_alta() -> None:
    resultado = analisar_registrabilidade(
        matriz=_matriz(impedimentos=1),
        pontuacao_risco=79,
        nivel_risco="critico",
        previsao=_previsao(probabilidade=0.92),
    )

    assert resultado.abstencao is False
    assert resultado.decisao == "cenario_desfavoravel"
    assert resultado.status == "revisao_humana"


def test_agente_indica_cenario_favoravel_com_cobertura_suficiente() -> None:
    resultado = analisar_registrabilidade(
        matriz=_matriz(),
        pontuacao_risco=12,
        nivel_risco="baixo",
        previsao=_previsao(probabilidade=0.81),
    )

    assert resultado.abstencao is False
    assert resultado.decisao == "cenario_favoravel"
    assert resultado.status == "concluida"


def test_agente_nao_inventa_probabilidade_sem_modelo() -> None:
    resultado = analisar_registrabilidade(
        matriz=_matriz(),
        pontuacao_risco=12,
        nivel_risco="baixo",
        previsao=None,
    )

    assert resultado.probabilidade_deferimento is None
    assert resultado.decisao == "dados_insuficientes"
    assert "modelo estatístico indisponível" in resultado.motivos


async def test_reprocessa_pesquisa_com_relatorio_e_sem_execucao() -> None:
    agora = datetime.now(UTC)
    pesquisa = PesquisaMarca(
        id="pesquisa-pendente",
        organizacao_id=1,
        marca="ACME",
        atividade="Tecnologia",
        tipo_pesquisa="completa",
        criado_em=agora,
    )
    versao = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=1,
        schema_versao="relatorio-marca-4.2",
        conteudo_hash="hash-pendente",
        payload={"total": 0, "itens": [], "classes_atividade": []},
        gerado_em=agora,
    )
    sessao = FakeSession(
        [
            FakeResult(itens=[pesquisa]),
            FakeResult(scalar=versao),
            FakeResult(scalar=None),
            FakeResult(itens=[]),
            FakeResult(scalar=None),
        ]
    )

    resultado = await reprocessar_agentes_pendentes(sessao, organizacao_id=1)

    assert resultado == {
        "status": "concluido",
        "pendentes_encontradas": 1,
        "processadas": 1,
    }
    assert any(isinstance(item, ExecucaoAgenteRegistrabilidade) for item in sessao.adicionados)

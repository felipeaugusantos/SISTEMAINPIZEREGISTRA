from datetime import UTC, datetime

from app.api.pesquisas import construir_resumo_publico
from app.schemas import ClasseNiceCandidataResponse, RelatorioMarcaResponse


def _relatorio_minimo(*, analise_consolidada: dict | None) -> RelatorioMarcaResponse:
    return RelatorioMarcaResponse(
        id="abc",
        marca="CAVALINHO FEROZ",
        atividade="Venda de roupas",
        tipo_pesquisa="completa",
        classe_nice=None,
        criado_em=datetime.now(UTC),
        gerado_em=datetime.now(UTC),
        ultima_rpi=2897,
        classes_atividade=[
            ClasseNiceCandidataResponse(codigo="25", titulo="Vestuário", tipo="produto", termos_encontrados=["roupas"])
        ],
        matriz_afinidade_status="pendente_de_validacao",
        alto_renome_atualizado_em=None,
        total=0,
        limite_exibido=0,
        itens=[],
        risco_pontuacao=69,
        risco_nivel="alto",
        analise_consolidada=analise_consolidada,
    )


def _analise_favoravel() -> dict:
    return {
        "titulo": "Cenário preliminar favorável",
        "recomendacao": "Submeter a análise à revisão humana antes de qualquer decisão de depósito.",
        "conclusao_preliminar": {"decisao": "cenario_favoravel", "motivos": [], "nivel_risco": "baixo"},
        "matriz": {"regras": []},
        "estatistica": {"disponivel": False, "mensagem": ""},
        "diretriz_acao": {"codigo": "deposito_imediato", "rotulo": "Depósito imediato", "origem": "automatica"},
        "disclaimers": [
            {"codigo": "soberania_inpi", "titulo": "Soberania do INPI", "texto": "A decisão final cabe ao INPI."}
        ],
    }


def test_resumo_publico_expoe_veredito_unificado_quando_analise_existe() -> None:
    relatorio = _relatorio_minimo(analise_consolidada=_analise_favoravel())

    resumo = construir_resumo_publico(relatorio)

    assert resumo.analise_consolidada is not None
    assert resumo.analise_consolidada.situacao_codigo == "favoravel"
    assert resumo.analise_consolidada.situacao_rotulo == "Favorável"
    assert resumo.analise_consolidada.diretriz_acao == "deposito_imediato"
    assert resumo.analise_consolidada.disclaimers[0].codigo == "soberania_inpi"


def test_resumo_publico_sem_analise_consolidada_nao_quebra() -> None:
    relatorio = _relatorio_minimo(analise_consolidada=None)

    resumo = construir_resumo_publico(relatorio)

    assert resumo.analise_consolidada is None

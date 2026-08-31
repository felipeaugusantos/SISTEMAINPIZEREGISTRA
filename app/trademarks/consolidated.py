"""Uma conclusão automática por snapshot; revisão humana não altera o score do motor."""

import hashlib
import json
from copy import deepcopy

from app.trademarks.agent import analisar_registrabilidade, resultado_para_dict
from app.trademarks.registrability import construir_matriz_registrabilidade

VERSAO_ANALISE = "analise-unificada-1.0"


def construir_analise_consolidada(relatorio: dict, dados_complementares: dict | None = None) -> dict:
    dados = deepcopy(dados_complementares or {})
    entrada = {
        chave: valor
        for chave, valor in relatorio.items()
        if chave not in {"analise_consolidada", "versao", "schema_versao", "gerado_em", "conteudo_hash"}
    }
    assinatura = hashlib.sha256(
        json.dumps(
            {"relatorio": entrada, "complementos": dados, "motor": VERSAO_ANALISE},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()
    matriz = construir_matriz_registrabilidade(
        marca=relatorio.get("marca", ""),
        atividade=relatorio.get("atividade"),
        classe_nice=relatorio.get("classe_nice"),
        relatorio=entrada,
        pontuacao_risco=relatorio.get("risco_pontuacao"),
        nivel_risco=relatorio.get("risco_nivel"),
        dados_complementares=dados,
    )
    estimativa = relatorio.get("estimativa_registrabilidade") or {}
    estatistica_disponivel = (
        relatorio.get("estimativa_status") == "disponivel"
        and estimativa.get("modelo_status") == "ACTIVE"
        and estimativa.get("probabilidade_deferimento") is not None
        and float(estimativa.get("cobertura_entrada") or 0) >= 0.60
    )
    resultado = analisar_registrabilidade(
        matriz=matriz,
        pontuacao_risco=relatorio.get("risco_pontuacao"),
        nivel_risco=relatorio.get("risco_nivel"),
        previsao=estimativa if estatistica_disponivel else None,
    )
    titulos = {
        "dados_insuficientes": "Análise preliminar com informações pendentes",
        "cenario_desfavoravel": "Riscos relevantes identificados",
        "cenario_intermediario": "Pontos de atenção identificados",
        "cenario_favoravel": "Cenário preliminar favorável",
    }
    recomendacoes = {
        "dados_insuficientes": "Complementar as informações indicadas e submeter as evidências ao especialista.",
        "cenario_desfavoravel": "Revisar os conflitos e possíveis impedimentos antes de definir a estratégia de depósito.",
        "cenario_intermediario": "Revisar as anterioridades e a especificação de produtos e serviços.",
        "cenario_favoravel": "Submeter a análise à revisão humana antes de qualquer decisão de depósito.",
    }
    pendencias = [
        {
            "criterio": regra.get("criterio") or regra.get("codigo") or "Critério pendente",
            "descricao": regra.get("conclusao") or regra.get("justificativa") or "Requer avaliação técnica.",
        }
        for regra in matriz.get("regras", [])
        if regra.get("status") == "nao_analisado"
    ]
    anterior = relatorio.get("analise_consolidada") or {}
    parecer = deepcopy(anterior.get("parecer_humano")) if anterior.get("entrada_hash") == assinatura else None
    return {
        "versao_motor": VERSAO_ANALISE,
        "entrada_hash": assinatura,
        "titulo": titulos[resultado.decisao],
        "conclusao_preliminar": resultado_para_dict(resultado),
        "recomendacao": recomendacoes[resultado.decisao],
        "analise_conjunto": {
            "marca": relatorio.get("marca"),
            "atividade": relatorio.get("atividade"),
            "significado_informado": dados.get("significado"),
            "produtos_servicos": dados.get("produtos_servicos"),
            "descricao_visual": dados.get("descricao_visual"),
            "aviso": "A interpretação do conjunto e do mercado depende das evidências e da revisão do especialista.",
        },
        "anterioridades": [
            {
                chave: item.get(chave)
                for chave in (
                    "numero",
                    "titulo",
                    "situacao",
                    "criterios_encontro",
                    "afinidade_classes",
                    "relevancia_rotulo",
                )
            }
            for item in relatorio.get("itens", [])[:20]
        ],
        "pendencias": pendencias,
        "matriz": matriz,
        "estatistica": {
            "disponivel": estatistica_disponivel,
            "mensagem": (
                "Estimativa histórica de apoio; não é garantia de registro."
                if estatistica_disponivel
                else "Estimativa estatística indisponível: modelo ausente, em validação ou sem elegibilidade. "
                "A análise utiliza as evidências da pesquisa e os critérios técnicos disponíveis."
            ),
            "estimativa": deepcopy(estimativa) if estatistica_disponivel else None,
        },
        "fontes": {"ultima_rpi": relatorio.get("ultima_rpi"), "qualidade_base": relatorio.get("qualidade_base")},
        "dados_complementares": dados,
        "parecer_humano": parecer,
    }


def analise_para_exibicao(relatorio: dict, *, versao: int, validado_por=None, validado_em=None) -> dict:
    """Tela e PDF usam o mesmo snapshot; documentos legados ganham uma leitura compatível."""
    armazenada = relatorio.get("analise_consolidada")
    analise = deepcopy(armazenada) if armazenada else construir_analise_consolidada(relatorio)
    analise["versao_relatorio"] = versao
    analise["legado"] = not bool(armazenada)
    # Uma interpretação nova de um snapshot legado nunca herda chancela técnica antiga.
    validada = bool(armazenada and analise.get("parecer_humano") and validado_por and validado_em)
    analise["revisao"] = {
        "validada": validada,
        "validado_por": validado_por if validada else None,
        "validado_em": validado_em.isoformat() if validada and hasattr(validado_em, "isoformat") else None,
    }
    return analise
